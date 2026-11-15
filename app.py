import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from catalog import ARABIC_SAMPLES, BUILTIN_VOICES, DIALECTS, EMOTIONS, FLAGS, INDEX_ENGLISH_VOICES, INDEX_LANGUAGES, MODELS, QWEN_LANGUAGES, REFERENCE_VOICES, SPEAKERS, TASHKIL, controls, defaults, settings_from
from catalog import CHATTERBOX_LANGUAGES, HIGGS_MODELS, REFERENCE_MODELS, REFERENCE_TRANSCRIPTS, REQUIRED_REFERENCE
from documents import IMPORT_EXTENSIONS, MAX_TEXT, import_document
from queue_worker import Worker
import shares
from storage import JOBS, REFERENCES, ROOT, VOICES, connect, one, rows, setup
from system_stats import Stats
from voice_media import store_voice


@asynccontextmanager
async def lifespan(app):
    setup()
    worker = Worker()
    worker.start()
    app.state.share_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mp3")

    async def clear_shares():
        while True:
            await run_in_threadpool(shares.cleanup)
            await asyncio.sleep(3600)

    cleanup = asyncio.create_task(clear_shares())
    try:
        yield
    finally:
        cleanup.cancel()
        await asyncio.gather(cleanup, return_exceptions=True)
        await run_in_threadpool(app.state.share_worker.shutdown, wait=False, cancel_futures=True)
        await run_in_threadpool(worker.close)


app = FastAPI(title="AI Speech Studio", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")
system_stats = Stats()
studio_css_version = hashlib.sha256((ROOT / "static/studio.css").read_bytes()).hexdigest()[:12]
studio_js_version = hashlib.sha256((ROOT / "static/studio.js").read_bytes()).hexdigest()[:12]
trusted_origins = {origin.strip().rstrip("/").lower()
                   for origin in os.getenv("TTS_TRUSTED_ORIGINS", "").split(",") if origin.strip()}
templates.env.globals.update(models=MODELS, dialects=DIALECTS, languages=QWEN_LANGUAGES, speakers=SPEAKERS,
                             flags=FLAGS, controls=controls, tashkil=TASHKIL, builtin_voices=BUILTIN_VOICES,
                             index_languages=INDEX_LANGUAGES, chatterbox_languages=CHATTERBOX_LANGUAGES, higgs_models=HIGGS_MODELS,
                             reference_transcripts=REFERENCE_TRANSCRIPTS, english_voices=INDEX_ENGLISH_VOICES,
                             emotions=EMOTIONS, arabic_samples=ARABIC_SAMPLES,
                             import_extensions=",".join(IMPORT_EXTENSIONS), max_text=MAX_TEXT,
                             studio_js_version=studio_js_version, studio_css_version=studio_css_version)


@app.middleware("http")
async def prevent_cross_site_posts(request, call_next):
    if request.method == "POST":
        site = request.headers.get("sec-fetch-site")
        origin = request.headers.get("origin", "").lower()
        try:
            same_host = bool(origin) and urlsplit(origin).netloc == request.headers.get("host", "").lower()
        except ValueError:
            same_host = False
        # Browsers compare the public origin before the proxy can change the upstream Host.
        if site in {"same-site", "cross-site"} or (
                origin and site != "same-origin" and not same_host and origin not in trusted_origins):
            return JSONResponse({"detail": "Open the studio directly and submit from that page."}, status_code=403)
    return await call_next(request)


def current_user(request):
    profile_id = request.cookies.get("studio_profile")
    return one("SELECT * FROM users WHERE id=?", (profile_id,)) or one(
        "SELECT users.* FROM users JOIN site_profile ON users.id=site_profile.user_id WHERE singleton=1")


def reference_file(uid, key):
    if not isinstance(key, str):
        return None
    if key.startswith("dialect:"):
        builtin = REFERENCE_VOICES.get(key.removeprefix("dialect:"))
        return REFERENCES / builtin[1] if builtin else None
    if key.startswith("previous:"):
        job = key.removeprefix("previous:")
        if one("SELECT id FROM jobs WHERE id=? AND user_id=?", (job, uid)):
            path = JOBS / job / "reference.wav"
            return path if path.is_file() else None
    if one("SELECT id FROM voices WHERE id=? AND user_id=?", (key, uid)):
        return VOICES / f"{key}.wav"


def render(request, template, **data):
    profile = current_user(request)
    response = templates.TemplateResponse(request, template, {"profile": profile, **data})
    if data.get("page"):
        response.headers["Cache-Control"] = "private, no-store"
        if request.cookies.get("studio_profile") != str(profile["id"]):
            remember_profile(response, profile["id"])
    return response


def remember_profile(response, uid):
    response.set_cookie("studio_profile", str(uid), httponly=True, samesite="lax", max_age=60*60*24*365)
    return response


def lookups(uid):
    return {"projects": rows("SELECT * FROM projects WHERE user_id=? ORDER BY name", (uid,)),
            "voices": rows("SELECT * FROM voices WHERE user_id=? ORDER BY name", (uid,)),
            "presets": rows("SELECT * FROM presets WHERE user_id=? ORDER BY name", (uid,))}


def decode_jobs(items):
    for item in items:
        item["config"] = json.loads(item["settings"])
    return items


def history(uid, project=None, limit=24):
    return decode_jobs(rows("""SELECT j.*, p.name AS project_name FROM jobs j LEFT JOIN projects p ON p.id=j.project_id
        WHERE j.user_id=? AND (? IS NULL OR j.project_id=?) ORDER BY j.created DESC,j.rowid DESC LIMIT ?""",
                            (uid, project, project, limit)))


def health_data():
    state = one("SELECT * FROM worker_state WHERE singleton=1")
    worker = bool(state and time.time() - state["last_seen"] < 10)
    count = one("SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','running')")["n"]
    return {"worker": worker, "loaded": state["model"] if worker else None, "pending": count,
            "lifecycle": {key: state[key] for key in ("phase", "step", "total", "target")} if worker else {}}


def job_for(uid, key):
    item = one("SELECT * FROM jobs WHERE user_id=? AND id=?", (uid, key))
    if item is None:
        raise HTTPException(404, "Job not found.")
    return item


def library_redirect(project):
    return RedirectResponse(f"/library?project={int(project)}" if str(project).isdigit() else "/library", 303)


@app.get("/health")
def health():
    return health_data()


@app.get("/", response_class=HTMLResponse)
def studio(request: Request, model: str = "qwen", preset: int | None = None, reuse: str | None = None,
           uploaded_voice: str = "", uploaded_emotion: str = ""):
    uid = current_user(request)["id"]
    saved = one("SELECT * FROM presets WHERE id=? AND user_id=?", (preset, uid)) if preset else None
    previous = job_for(uid, reuse) if reuse else None
    source = previous or saved
    model = source["model"] if source else model
    if model not in MODELS:
        model = "qwen"
    config = defaults(model)
    if source:
        config.update(json.loads(source["settings"]))
    if config.get("voice_id") and not reference_file(uid, config["voice_id"]):
        config["voice_id"] = ""
    previous_voice = f"previous:{reuse}" if previous and reference_file(uid, f"previous:{reuse}") else ""
    if previous_voice and not config.get("voice_id"):
        config["voice_id"] = previous_voice
    if config.get("emo_voice") and not reference_file(uid, config["emo_voice"]):
        config["emo_voice"] = ""
    if model in REFERENCE_MODELS and uploaded_voice and reference_file(uid, uploaded_voice):
        config["voice_id"] = uploaded_voice
    if model == "index" and uploaded_emotion and reference_file(uid, uploaded_emotion):
        config.update(emo_mode="audio", emo_voice=uploaded_emotion)
    return render(request, "studio.html", page="studio", model=model, config=config, reused=previous, previous_voice=previous_voice,
                  text=previous["script"] if previous else "", jobs=history(uid, limit=8), state=health_data(), **lookups(uid))


@app.get("/controls", response_class=HTMLResponse)
def model_controls(request: Request, model: str = "qwen", reused: str = ""):
    if model not in MODELS:
        raise HTTPException(404, "Model not found.")
    uid = current_user(request)["id"]
    previous_voice = f"previous:{reused}" if reused and reference_file(uid, f"previous:{reused}") else ""
    return render(request, "controls.html", model=model, config=defaults(model), previous_voice=previous_voice, **lookups(uid))


@app.get("/system-stats")
def server_stats():
    return JSONResponse(system_stats.sample(), headers={"Cache-Control": "no-store"})


@app.get("/activity", response_class=HTMLResponse)
def activity(request: Request):
    return render(request, "activity.html", jobs=history(current_user(request)["id"], limit=8), state=health_data())


@app.get("/library", response_class=HTMLResponse)
def library(request: Request, project: str = ""):
    uid = current_user(request)["id"]
    selected = one("SELECT id FROM projects WHERE id=? AND user_id=?", (project, uid))
    project = selected["id"] if selected else None
    return render(request, "library.html", page="library", jobs=history(uid, project, 150), selected=project,
                  default_profile=one("SELECT user_id FROM site_profile WHERE singleton=1")["user_id"],
                  profiles=rows("SELECT * FROM users ORDER BY name COLLATE NOCASE"), **lookups(uid))


@app.get("/voices", response_class=HTMLResponse)
def voices_library(request: Request, uploaded: int = 0, skipped: int = 0):
    voices = rows("SELECT * FROM voices WHERE user_id=? ORDER BY name COLLATE NOCASE, created DESC",
                  (current_user(request)["id"],))
    return render(request, "voices.html", page="voices", voices=voices, uploaded=uploaded, skipped=skipped)


@app.post("/import-text")
async def extract_text(request: Request):
    async with request.form(max_files=1, max_fields=0) as form:
        upload = form.get("document")
        if not isinstance(upload, UploadFile) or not upload.filename:
            raise HTTPException(400, "Choose a text or document file.")
        try:
            text = await run_in_threadpool(import_document, upload)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"text": text}


@app.get("/references/{dialect}")
def reference_audio(dialect: str):
    config = REFERENCE_VOICES.get(dialect)
    if not config:
        raise HTTPException(404, "No reference for this dialect.")
    return FileResponse(REFERENCES / config[1])


@app.post("/voices/upload")
async def upload_voice(request: Request):
    uid = current_user(request)["id"]
    try:
        async with request.form(max_fields=1) as form:
            view = form.get("return_view")
            native = view in REFERENCE_MODELS | {"voices", "index-emotion"}
            files = [file for file in form.getlist("reference") if isinstance(file, UploadFile) and file.filename]
            if not files or (len(files) > 1 and view != "voices"):
                raise HTTPException(400, "Choose a reference audio file.")
            saved = []
            errors = []
            for file in files:
                try:
                    saved.append(await run_in_threadpool(store_voice, uid, file))
                except ValueError as exc:
                    errors.append(str(exc))
            if not saved:
                raise HTTPException(400, errors[0])
    except ClientDisconnect as exc:
        raise HTTPException(400, "The upload connection closed before the file arrived.") from exc
    if not native:
        return one("SELECT id,name FROM voices WHERE id=? AND user_id=?", (saved[0], uid))
    if view == "voices":
        return RedirectResponse(f"/voices?uploaded={len(saved)}&skipped={len(errors)}", 303)
    model = "index" if view == "index-emotion" else view
    field = "uploaded_emotion" if view == "index-emotion" else "uploaded_voice"
    return RedirectResponse("/?" + urlencode({"model": model, field: saved[0]}), 303)


@app.get("/voices/{key}/audio")
def voice_audio(request: Request, key: str, download: bool = False, reference: bool = False):
    voice = one("SELECT * FROM voices WHERE id=? AND user_id=?", (key, current_user(request)["id"]))
    if not voice:
        raise HTTPException(404, "Voice not found.")
    headers = {"X-Content-Type-Options": "nosniff"}
    original = VOICES / f"{key}.source"
    if not reference and original.is_file():
        if download:
            return FileResponse(original, media_type="application/octet-stream",
                                filename=voice["original_name"], headers=headers)
        if voice["media_type"]:
            return FileResponse(original, media_type=voice["media_type"], headers=headers)
        return FileResponse(VOICES / f"{key}.mp3", media_type="audio/mpeg", headers=headers)
    return FileResponse(VOICES / f"{key}.wav", media_type="audio/wav",
                        filename=f"{voice['name']}.wav" if download else None, headers=headers)


@app.get("/jobs/{key}/audio")
def job_audio(request: Request, key: str, download: bool = False):
    job = job_for(current_user(request)["id"], key)
    extension = json.loads(job["settings"])["format"]
    path = JOBS / key / f"speech.{extension}"
    if job["status"] != "finished" or not path.is_file():
        raise HTTPException(404, "Audio is not available yet.")
    return FileResponse(path, filename=f"{job['title']}.{extension}" if download else None)


@app.get("/jobs/{key}/reference")
def job_reference_audio(request: Request, key: str):
    path = reference_file(current_user(request)["id"], f"previous:{key}")
    if not path:
        raise HTTPException(404, "Reference audio unavailable.")
    # A copied built-in reference may originally have been FLAC.
    with path.open("rb") as file:
        media_type = "audio/flac" if file.read(4) == b"fLaC" else "audio/wav"
    return FileResponse(path, media_type=media_type)


def share_source(request, key):
    job = job_for(current_user(request)["id"], key)
    path = JOBS / key / f"speech.{json.loads(job['settings'])['format']}"
    if job["status"] != "finished" or not job["duration"] or not path.is_file():
        raise HTTPException(404, "Recording unavailable.")
    return job, path


@app.get("/jobs/{key}/share")
def share_options(request: Request, key: str):
    job, path = share_source(request, key)
    options = shares.options(path, job["duration"])
    for option in options:
        option.update(shares.status(key, option["rate"]))
    return {"options": options}


@app.post("/jobs/{key}/share/{rate}")
def create_share(request: Request, key: str, rate: int):
    job, path = share_source(request, key)
    if rate not in {option["rate"] for option in shares.options(path, job["duration"])}:
        raise HTTPException(400, "Choose a smaller MP3 preset.")
    return shares.start(request.app.state.share_worker, key, rate, path, job["duration"])


@app.get("/jobs/{key}/share/{rate}")
def share_status(request: Request, key: str, rate: int, download: bool = False):
    job, _ = share_source(request, key)
    if rate not in shares.BITRATES:
        raise HTTPException(404, "Unknown MP3 preset.")
    if not download:
        return shares.status(key, rate)
    path = shares.path_for(key, rate)
    if not shares.available(path):
        raise HTTPException(404, "The share expired. Create a new copy.")
    return FileResponse(path, filename=f"{job['title']} - {rate} kbps.mp3", media_type="audio/mpeg")


def submit_error(request, message):
    if request.headers.get("HX-Request"):
        return render(request, "notice.html", message=message, error=True)
    raise HTTPException(400, message)


def name_from(form, key="name", default=""):
    name = form.get(key, "").strip() or default
    if not name or len(name) > 100:
        raise ValueError("Enter a name between 1 and 100 characters.")
    return name


def notify_voice(response, voice, queue=False):
    events = {"queueUpdated": True} if queue else {}
    if voice:
        events["voiceSaved"] = {"id": voice["id"], "name": voice["name"]}
    if events:
        response.headers["HX-Trigger"] = json.dumps(events)
    return response


@app.post("/jobs")
async def create_job(request: Request):
    uid = current_user(request)["id"]
    async with request.form(max_files=2, max_fields=100, max_part_size=MAX_TEXT * 4) as form:
        model = form.get("model", "")
        if model not in MODELS:
            return submit_error(request, "Choose a TTS model.")
        key = uuid.uuid4().hex
        folder = JOBS / key
        saved_voice = None
        try:
            config = settings_from(form, model)
            project = form.get("project") or None
            if project and not one("SELECT id FROM projects WHERE id=? AND user_id=?", (project, uid)):
                raise ValueError("Choose a project from your library.")
            selected_voice = config.get("voice_id")
            if selected_voice and not reference_file(uid, selected_voice):
                raise ValueError("Choose a reference voice from your library.")
            emotion_voice = config.get("emo_voice")
            if config.get("emo_mode") == "audio" and (not emotion_voice or not reference_file(uid, emotion_voice)):
                raise ValueError("Choose a reference recording for the emotion.")
            is_preset = form.get("intent") == "preset"
            reference = form.get("reference")
            if is_preset:
                title = name_from(form, "preset_name")
            else:
                text = form.get("script", "").strip()
                if not text or len(text) > MAX_TEXT:
                    raise ValueError("Enter between 1 and 150,000 characters of text.")
                title = name_from(form, "title", " ".join(text.split())[:60])
            if model in REFERENCE_MODELS and isinstance(reference, UploadFile) and reference.filename:
                selected_voice = await run_in_threadpool(store_voice, uid, reference)
                config["voice_id"] = selected_voice
                saved_voice = one("SELECT id,name FROM voices WHERE id=?", (selected_voice,))
            if model in REQUIRED_REFERENCE and not selected_voice:
                raise ValueError(f"Choose or upload a reference voice for {MODELS[model]}.")
            if model == "qwen_base" and not config["x_vector_only"]:
                if not (config["reference_text"] or REFERENCE_TRANSCRIPTS.get(selected_voice)):
                    raise ValueError("Enter the reference transcript or enable Clone without transcript.")
            if model in HIGGS_MODELS and not selected_voice and config["reference_text"]:
                raise ValueError("Choose the recording that matches the reference transcript.")
            if is_preset:
                with connect() as db:
                    db.execute("INSERT INTO presets(user_id,name,model,settings) VALUES (?,?,?,?) ON CONFLICT(user_id,name) DO UPDATE SET model=excluded.model, settings=excluded.settings",
                               (uid, title, model, json.dumps(config)))
                response = render(request, "notice.html", message=f"Saved preset {title}. Find it under Library.", error=False)
                return notify_voice(response, saved_voice)
            folder.mkdir()
            if model in REFERENCE_MODELS:
                dest = folder / "reference.wav"
                if selected_voice:
                    shutil.copyfile(reference_file(uid, selected_voice), dest)
            if emotion_voice:
                shutil.copyfile(reference_file(uid, emotion_voice), folder / "emotion.wav")
            with connect() as db:
                db.execute("INSERT INTO jobs(id,user_id,project_id,model,title,script,settings,status) VALUES (?,?,?,?,?,?,?,'queued')",
                           (key, uid, project, model, title, text, json.dumps(config)))
        except (ValueError, OSError, sqlite3.IntegrityError, HTTPException) as exc:
            shutil.rmtree(folder, ignore_errors=True)
            return submit_error(request, str(exc))
    if request.headers.get("HX-Request"):
        response = render(request, "notice.html", message="Job added to the queue.", error=False)
        return notify_voice(response, saved_voice, queue=True)
    return RedirectResponse("/", 303)


@app.post("/jobs/{key}/cancel")
def cancel_job(request: Request, key: str):
    uid = current_user(request)["id"]
    job_for(uid, key)
    with connect() as db:
        db.execute("UPDATE jobs SET cancel=1, status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END, phase='Cancelling',updated=CURRENT_TIMESTAMP WHERE id=? AND status IN ('queued','running')", (key,))
        db.execute("UPDATE jobs SET phase='Cancelled' WHERE id=? AND status='cancelled'", (key,))
    return activity(request) if request.headers.get("HX-Request") else RedirectResponse("/library", 303)


@app.post("/jobs/{key}/rename")
async def rename_job(request: Request, key: str):
    uid = current_user(request)["id"]
    async with request.form(max_fields=2) as form:
        try:
            title = name_from(form)
        except ValueError as exc:
            return submit_error(request, str(exc))
        with connect() as db:
            changed = db.execute("UPDATE jobs SET title=? WHERE id=? AND user_id=?",
                                 (title, key, uid)).rowcount
        if not changed:
            raise HTTPException(404, "Recording not found.")
        return library_redirect(form.get("view"))


def edit_recordings(uid, keys, action, project=None):
    if not keys or action not in {"delete", "move"}:
        raise HTTPException(400, "Select recordings and an action.")
    placeholders = ",".join("?" for _ in keys)
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        records = db.execute(f"SELECT id,status FROM jobs WHERE user_id=? AND id IN ({placeholders})",
                             (uid, *keys)).fetchall()
        if len(records) != len(keys):
            raise HTTPException(404, "A selected recording is no longer available.")
        if action == "move":
            if project and not db.execute("SELECT id FROM projects WHERE id=? AND user_id=?", (project, uid)).fetchone():
                raise HTTPException(400, "Choose a project from your library.")
            db.execute(f"UPDATE jobs SET project_id=? WHERE user_id=? AND id IN ({placeholders})",
                       (project or None, uid, *keys))
        else:
            if any(row["status"] in {"running", "queued"} for row in records):
                raise HTTPException(409, "Cancel active jobs before deleting them.")
            db.execute(f"DELETE FROM jobs WHERE user_id=? AND id IN ({placeholders})", (uid, *keys))
    if action == "delete":
        for key in keys:
            shutil.rmtree(JOBS / key, ignore_errors=True)


@app.post("/jobs/{key}/delete")
async def delete_job(request: Request, key: str):
    async with request.form(max_fields=1) as form:
        edit_recordings(current_user(request)["id"], [key], "delete")
        return library_redirect(form.get("view"))


@app.post("/library/recordings")
async def manage_recordings(request: Request):
    async with request.form(max_fields=160) as form:
        edit_recordings(current_user(request)["id"], list(dict.fromkeys(form.getlist("recording"))),
                        form.get("intent"), form.get("destination"))
        return library_redirect(form.get("view"))


@app.post("/library/{kind}")
async def save_library(request: Request, kind: str):
    uid = current_user(request)["id"]
    async with request.form(max_files=1, max_fields=5) as form:
        try:
            name = name_from(form)
            if kind == "profiles":
                with connect() as db:
                    db.execute("INSERT OR IGNORE INTO users(name) VALUES (?)", (name,))
                    chosen = db.execute("SELECT id FROM users WHERE name=?", (name,)).fetchone()[0]
                return remember_profile(RedirectResponse("/library#profiles", 303), chosen)
            if kind == "projects":
                with connect() as db:
                    db.execute("INSERT OR IGNORE INTO projects(user_id,name) VALUES (?,?)", (uid, name))
            elif kind == "voices":
                upload = form.get("reference")
                if not isinstance(upload, UploadFile) or not upload.filename:
                    raise ValueError("Choose a reference audio file.")
                await run_in_threadpool(store_voice, uid, upload, name)
                if request.headers.get("HX-Request"):
                    return HTMLResponse(headers={"HX-Redirect": "/voices"})
                return RedirectResponse("/voices", 303)
            else:
                raise HTTPException(404, "Unknown library type.")
        except ValueError as exc:
            return submit_error(request, str(exc))
    return RedirectResponse("/library", 303)


@app.post("/voices/{key}/rename")
async def rename_voice(request: Request, key: str):
    async with request.form(max_files=0, max_fields=1) as form:
        try:
            name = name_from(form)
        except ValueError as exc:
            return submit_error(request, str(exc))
    with connect() as db:
        changed = db.execute("UPDATE voices SET name=? WHERE id=? AND user_id=?",
                             (name, key, current_user(request)["id"])).rowcount
    if not changed:
        raise HTTPException(404, "Voice not found.")
    return JSONResponse({"id": key, "name": name}) if request.headers.get("accept") == "application/json" else RedirectResponse("/voices", 303)


@app.post("/profiles/select")
async def choose_profile(request: Request):
    async with request.form(max_fields=2) as form:
        profile = one("SELECT id FROM users WHERE id=?", (form.get("profile"),))
        if not profile:
            raise HTTPException(404, "Profile not found.")
        if form.get("intent") == "default":
            with connect() as db:
                db.execute("UPDATE site_profile SET user_id=? WHERE singleton=1", (profile["id"],))
    return remember_profile(RedirectResponse("/library#profiles", 303), profile["id"])


@app.post("/library/{kind}/{key}/delete")
def delete_library(request: Request, kind: str, key: str):
    if kind not in ("projects", "presets", "voices"):
        raise HTTPException(404, "Unknown library type.")
    with connect() as db:
        removed = db.execute(f"DELETE FROM {kind} WHERE id=? AND user_id=?", (key, current_user(request)["id"])).rowcount
    if removed and kind == "voices":
        for suffix in ("wav", "source", "mp3"):
            (VOICES / f"{key}.{suffix}").unlink(missing_ok=True)
    return RedirectResponse("/voices" if kind == "voices" else "/library", 303)
