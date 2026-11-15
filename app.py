import json
import shutil
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile
from starlette.concurrency import run_in_threadpool

from catalog import DIALECTS, EGYPTIAN_EXAMPLES, FLAGS, MODELS, QWEN_LANGUAGES, SPEAKERS, TASHKIL, controls, defaults, settings_from
from documents import IMPORT_EXTENSIONS, MAX_TEXT, import_document
from queue_worker import Worker
from storage import JOBS, REFERENCES, ROOT, VOICES, connect, one, rows, setup
from voice_media import store_voice


@asynccontextmanager
async def lifespan(app):
    setup()
    worker = Worker()
    worker.start()
    yield
    await run_in_threadpool(worker.close)


app = FastAPI(title="AI Speech Studio", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")
templates.env.globals.update(models=MODELS, dialects=DIALECTS, languages=QWEN_LANGUAGES, speakers=SPEAKERS,
                             flags=FLAGS, controls=controls, tashkil=TASHKIL, egyptian_examples=EGYPTIAN_EXAMPLES,
                             import_extensions=",".join(IMPORT_EXTENSIONS), max_text=MAX_TEXT)


@app.middleware("http")
async def prevent_cross_site_posts(request, call_next):
    origin = request.headers.get("origin")
    if request.method == "POST" and origin and urlsplit(origin).netloc != request.headers.get("host"):
        return JSONResponse({"detail": "Submit from the studio page."}, status_code=403)
    return await call_next(request)


def current_user(request):
    profile_id = request.cookies.get("studio_profile", "1")
    return one("SELECT * FROM users WHERE id=?", (profile_id,)) or one("SELECT * FROM users WHERE id=1")


def render(request, template, **data):
    return templates.TemplateResponse(request, template, {"profile": current_user(request), **data})


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


@app.get("/health")
def health():
    return health_data()


@app.get("/", response_class=HTMLResponse)
def studio(request: Request, model: str = "qwen", preset: int | None = None, reuse: str | None = None):
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
    if config.get("voice_id") and not one("SELECT id FROM voices WHERE id=? AND user_id=?", (config["voice_id"], uid)):
        config["voice_id"] = ""
    return render(request, "studio.html", page="studio", model=model, config=config, reused=previous,
                  text=previous["script"] if previous else "", jobs=history(uid, limit=8), state=health_data(), **lookups(uid))


@app.get("/controls", response_class=HTMLResponse)
def model_controls(request: Request, model: str = "qwen"):
    if model not in MODELS:
        raise HTTPException(404, "Model not found.")
    return render(request, "controls.html", model=model, config=defaults(model), **lookups(current_user(request)["id"]))


@app.get("/activity", response_class=HTMLResponse)
def activity(request: Request):
    return render(request, "activity.html", jobs=history(current_user(request)["id"], limit=8), state=health_data())


@app.get("/library", response_class=HTMLResponse)
def library(request: Request, project: int | None = None):
    uid = current_user(request)["id"]
    return render(request, "library.html", page="library", jobs=history(uid, project, 150), selected=project,
                  profiles=rows("SELECT * FROM users ORDER BY name"), **lookups(uid))


@app.get("/voices", response_class=HTMLResponse)
def voices_library(request: Request):
    voices = rows("SELECT * FROM voices WHERE user_id=? ORDER BY name COLLATE NOCASE, created DESC",
                  (current_user(request)["id"],))
    return render(request, "voices.html", page="voices", voices=voices)


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
    config = DIALECTS.get(dialect)
    if not config or not config[1]:
        raise HTTPException(404, "No reference for this dialect.")
    return FileResponse(REFERENCES / config[1])


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
    async with request.form(max_files=2, max_fields=60, max_part_size=512000) as form:
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
            if selected_voice and not one("SELECT id FROM voices WHERE id=? AND user_id=?", (selected_voice, uid)):
                raise ValueError("Choose a reference voice from your library.")
            is_preset = form.get("intent") == "preset"
            reference = form.get("reference")
            if is_preset:
                title = name_from(form, "preset_name")
            else:
                text = form.get("script", "").strip()
                if not text or len(text) > MAX_TEXT:
                    raise ValueError("Enter between 1 and 150,000 characters of text.")
                title = name_from(form, "title", " ".join(text.split())[:60])
            if model == "lahgtna" and isinstance(reference, UploadFile) and reference.filename:
                selected_voice = await run_in_threadpool(store_voice, uid, reference)
                config["voice_id"] = selected_voice
                saved_voice = one("SELECT id,name FROM voices WHERE id=?", (selected_voice,))
            if is_preset:
                with connect() as db:
                    db.execute("INSERT INTO presets(user_id,name,model,settings) VALUES (?,?,?,?) ON CONFLICT(user_id,name) DO UPDATE SET model=excluded.model, settings=excluded.settings",
                               (uid, title, model, json.dumps(config)))
                response = render(request, "notice.html", message=f"Saved preset {title}. Find it under Library.", error=False)
                return notify_voice(response, saved_voice)
            folder.mkdir()
            reused = form.get("reused")
            if model == "lahgtna":
                dest = folder / "reference.wav"
                if selected_voice:
                    shutil.copyfile(VOICES / f"{selected_voice}.wav", dest)
                elif reused and job_for(uid, reused):
                    source = JOBS / reused / "reference.wav"
                    if source.is_file():
                        shutil.copyfile(source, dest)
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
                response = RedirectResponse("/library", 303)
                response.set_cookie("studio_profile", str(chosen), httponly=True, samesite="lax", max_age=60*60*24*365)
                return response
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
    return RedirectResponse("/voices", 303)


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
