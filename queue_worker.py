import fcntl
import json
import logging
import os
import re
import select
import shutil
import signal
import subprocess
import threading
import time
import unicodedata
from pathlib import Path

from generation_limits import SpeechLimitError
from notification_events import speech_event
from speech_assembly import SpeechAssembly
from storage import AISPEECH, DATA, JOBS, ROOT, connect, one


log = logging.getLogger(__name__)
MODEL_RUNTIME = {"qwen_base": "qwen", "higgs_bf16": "index", "higgs_q8": "index", "chatterbox": "lahgtna"}
MIN_DISK_FREE = 1024 ** 3
IDLE_SECONDS = float(os.getenv("TTS_MODEL_IDLE_SECONDS", "120"))


class Cancelled(Exception):
    pass


class WorkerStopping(Exception):
    pass


class ModelConnectionError(RuntimeError):
    pass


# Keep pronunciation annotations, delivery tags and expression tags together.
SPEECH_MARKUP = re.compile(r"<\|[^<>\n]+\|>|<[^<>\n]+\|[^<>\n]+>|\[[^\]\n]{1,40}\]")
SENTENCE_END = re.compile(r"[.!?؟؛。！？](?=\s|$)|\n\n")


def safe_cut(text, cut):
    for marker in SPEECH_MARKUP.finditer(text):
        if marker.start() < cut < marker.end():
            return marker.start() or marker.end()
        if marker.start() >= cut:
            break
    while cut > 1 and cut < len(text) and unicodedata.combining(text[cut]):
        cut -= 1
    return cut


def split_retry(text):
    middle = len(text) // 2
    lower, upper = max(8, len(text) // 4), min(len(text) - 8, len(text) * 3 // 4)
    boundaries = [m.end() for m in SENTENCE_END.finditer(text) if lower <= m.end() <= upper]
    if not boundaries:
        boundaries = [m.start() for m in re.finditer(r"\s+", text) if lower <= m.start() <= upper]
    cut = safe_cut(text, min(boundaries, key=lambda pos: abs(pos - middle)) if boundaries else middle)
    return text[:cut].strip(), text[cut:].strip()


def chunk_text(text, limit):
    start = 0
    while len(text) - start > limit:
        search = text[start:start + limit + 1]
        if not search.strip():
            start += len(search)
            continue
        ends = list(SENTENCE_END.finditer(search))
        spaces = list(re.finditer(r"\s", search))
        cut = ends[-1].end() if ends and ends[-1].end() >= limit // 3 else spaces[-1].start() if spaces else limit
        cut = max(1, safe_cut(text[start:start + limit + 256], cut if cut > 0 else limit))
        piece = text[start:start + cut].strip()
        if piece:
            yield piece
        start += cut
        while start < len(text) and text[start].isspace():
            start += 1
    if piece := text[start:].strip():
        yield piece


def update(job, **changes):
    with connect() as db:
        names = ",".join(f"{key}=?" for key in changes)
        db.execute(f"UPDATE jobs SET {names}, updated=CURRENT_TIMESTAMP WHERE id=?", (*changes.values(), job))
        if changes.get("status") in {"finished", "failed"}:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job,)).fetchone()
            speech_event(db, row, row["status"], duration=row["duration"], total=row["total"])


def claim():
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY queue_priority DESC,created,rowid LIMIT 1").fetchone()
        if row is None:
            return None
        db.execute("UPDATE jobs SET status='running', phase='Preparing', updated=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
        if row["queue_ahead"]:
            speech_event(db, row, "running")
        return dict(row)


class Worker:
    def __init__(self):
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.loop, name="tts-queue", daemon=True)
        self.process = None
        self.model = None
        self.lifecycle = ("idle", 0, 0, None)
        self.last_publish = 0
        self.output = bytearray()

    def start(self):
        self.thread.start()

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=12)

    def publish(self):
        self.last_publish = time.monotonic()
        with connect() as db:
            db.execute("INSERT OR REPLACE INTO worker_state(singleton,model,last_seen,phase,step,total,target) VALUES (1,?,?,?,?,?,?)",
                       (self.model, time.time(), *self.lifecycle))

    def progress(self, phase, step, total, model):
        self.lifecycle = (phase, step, total, model)
        self.publish()

    def unload(self):
        p = self.process
        if p is None:
            return
        model = self.model or self.lifecycle[3]
        self.progress("unloading", 0, 2, model)
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        self.progress("unloading", 1, 2, model)
        try:
            p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            pass
        # The native CUDA backend runs in this group and can outlive the Python leader.
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                os.killpg(p.pid, 0)
            except ProcessLookupError:
                break
            # A terminated grandchild can remain a zombie briefly. It owns no GPU state.
            for item in Path("/proc").glob("[0-9]*/stat"):
                try:
                    state, _, group, *_ = item.read_text().rsplit(")", 1)[1].split()
                except (OSError, ValueError, IndexError):
                    continue
                if group == str(p.pid) and state not in {"Z", "X"}:
                    break
            else:
                break
            time.sleep(.1)
        else:
            raise RuntimeError("The previous TTS process is still exiting. No new model was loaded.")
        p.stdin.close()
        p.stdout.close()
        self.process = self.model = None
        self.output.clear()
        self.progress("released", 2, 2, model)

    def check(self, job):
        row = one("SELECT cancel FROM jobs WHERE id=?", (job,))
        if not row or row["cancel"]:
            raise Cancelled
        if self.stop_event.is_set():
            raise WorkerStopping

    def receive(self, job, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check(job)
            if time.monotonic() - self.last_publish >= 2:
                self.publish()
            if b"\n" in self.output:
                line, _, self.output = self.output.partition(b"\n")
                result = json.loads(line)
                if "speech_limit" in result:
                    raise SpeechLimitError(result["speech_limit"])
                if "error" in result:
                    raise RuntimeError(result["error"])
                if "loaded_step" in result:
                    self.progress("loading", result["loaded_step"], 3, self.lifecycle[3])
                    continue
                return result
            if select.select([self.process.stdout], [], [], .4)[0]:
                data = os.read(self.process.stdout.fileno(), 65536)
                if not data:
                    raise ModelConnectionError("The model process exited. Check worker.log for the error.")
                self.output.extend(data)
        raise ModelConnectionError("The model timed out. Try a shorter chunk or check worker.log.")

    def load(self, model, job):
        if self.model == model and self.process.poll() is None:
            return
        self.unload()
        self.check(job)
        update(job, phase="Loading model")
        self.progress("loading", 0, 3, model)
        env = {**os.environ, "HF_HUB_CACHE": str(AISPEECH / "huggingface/hub"), "HF_HUB_OFFLINE": "1",
               "TOKENIZERS_PARALLELISM": "false", "OMP_NUM_THREADS": "2", "PYTHONUNBUFFERED": "1"}
        python = ROOT / ".runtime" / MODEL_RUNTIME.get(model, model) / "bin/python"
        with (DATA / "worker.log").open("ab") as dest:
            self.process = subprocess.Popen(
                [str(python), str(ROOT / "model_worker.py"), model], cwd=ROOT, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=dest, text=True, encoding="utf-8", bufsize=1, start_new_session=True,
            )
        if not self.receive(job, 240).get("ready"):
            raise RuntimeError("The model process failed to start.")
        self.model = model
        self.progress("ready", 3, 3, model)

    def send(self, job, payload, seconds=180):
        self.check(job)
        try:
            self.process.stdin.write(json.dumps(payload) + "\n")
            self.process.stdin.flush()
        except BrokenPipeError as exc:
            raise ModelConnectionError("The model closed its input stream.") from exc
        return self.receive(job, seconds)["ok"]

    def prepare(self, job, folder, script, settings):
        key = job["id"]
        self.load(job["model"], key)
        update(key, phase="Preparing voice")
        reference = folder / "reference.wav"
        prepared = self.send(key, {"op": "prepare", "script": script, "settings": settings,
                                   "reference": str(reference) if reference.exists() else None,
                                   "emotion": str(folder / "emotion.wav") if settings.get("emo_voice") else None})
        if prepared.get("dialect") and settings.get("dialect") == "auto":
            settings["detected_dialect"] = prepared["dialect"]
            update(key, settings=json.dumps(settings))
        update(key, phase="Generating speech")
        return prepared, True

    def render_chunk(self, key, text, path, settings, prepared, first, assembly, pause, depth=0):
        path.unlink(missing_ok=True)
        try:
            self.send(key, {"op": "render", "text": text, "output": str(path), "first": first,
                            "settings": settings, **prepared})
        except SpeechLimitError as exc:
            left, right = split_retry(text)
            if depth >= 5 or min(len(left), len(right)) < 8:
                raise SpeechLimitError(f"Chunk {assembly.index + 1} still reached the audio-token limit after shortening. "
                                       "Raise the audio-token limit in Advanced generation if available.") from exc
            self.render_chunk(key, left, path, settings, prepared, first, assembly, pause, depth + 1)
            self.render_chunk(key, right, path, settings, prepared, False, assembly, min(pause, 80), depth + 1)
        else:
            assembly.append(path, pause)

    def export(self, key, folder, assembly, settings):
        self.check(key)
        update(key, phase="Exporting audio")
        rate, channels = str(assembly.rate), str(assembly.channels)
        format = settings["format"]
        staged = folder / f"export.{format}"
        staged.unlink(missing_ok=True)
        args = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "s16le", "-ar", rate,
                "-ac", channels, "-i", str(assembly.path)]
        if settings["normalize"]:
            args += ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
        if format == "mp3":
            args += ["-codec:a", "libmp3lame", "-q:a", "2"]
        if format == "wav":
            args += ["-rf64", "auto"]
        with (DATA / "worker.log").open("ab") as log_file:
            process = subprocess.Popen([*args, "-ar", rate, str(staged)], stderr=log_file)
            try:
                last_size, last_progress = 0, time.monotonic()
                while process.poll() is None:
                    self.check(key)
                    if time.monotonic() - self.last_publish >= 2:
                        self.publish()
                    if shutil.disk_usage(folder).free < MIN_DISK_FREE:
                        raise RuntimeError("Free server disk space before exporting audio.")
                    size = staged.stat().st_size if staged.exists() else 0
                    if size > last_size:
                        last_size, last_progress = size, time.monotonic()
                    if time.monotonic() - last_progress > 180:
                        raise RuntimeError("Audio export stopped making progress. Check worker.log.")
                    self.stop_event.wait(.5)
                if process.returncode or not staged.is_file() or staged.stat().st_size == 0:
                    raise RuntimeError("Audio export failed. Check worker.log.")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        self.check(key)
        staged.replace(folder / f"speech.{format}")
        update(key, status="finished", phase="Ready", duration=assembly.duration)

    def run(self, job):
        key = job["id"]
        folder = JOBS / key
        folder.mkdir(exist_ok=True)
        part = folder / "chunk.wav"
        settings = json.loads(job["settings"])
        script = job["script"]
        if job["model"] == "lahgtna" and not settings["keep_tags"]:
            script = re.sub(r"\[[^\]\n]{1,40}\]", "", script)
        chunks = list(chunk_text(script, settings["chunk_size"]))
        if not chunks:
            raise ValueError("The text has no spoken content after removing tags.")
        assembly = SpeechAssembly(folder, chunks)
        update(key, total=len(chunks), done=assembly.index)
        if assembly.index < len(chunks):
            prepared, first = self.prepare(job, folder, script, settings)
            for index in range(assembly.index, len(chunks)):
                self.check(key)
                if shutil.disk_usage(folder).free < MIN_DISK_FREE:
                    raise RuntimeError("Generation stopped because the server has less than 1 GiB of free disk space.")
                try:
                    self.render_chunk(key, chunks[index], part, settings, prepared, first, assembly, settings["pause_ms"])
                except ModelConnectionError:
                    assembly.rewind()
                    self.unload()
                    prepared, first = self.prepare(job, folder, script, settings)
                    self.render_chunk(key, chunks[index], part, settings, prepared, first, assembly, settings["pause_ms"])
                first = False
                assembly.commit(index + 1)
                update(key, done=index + 1)
        self.export(key, folder, assembly, settings)

    def loop(self):
        with (DATA / "worker.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            with connect() as db:
                db.execute("UPDATE jobs SET status='cancelled', phase='Cancelled' WHERE status='running' AND cancel=1")
                db.execute("UPDATE jobs SET status='queued', phase='Waiting to resume', queue_priority=2 WHERE status='running'")
            last_used = time.monotonic()
            try:
                while not self.stop_event.is_set():
                    self.publish()
                    job = claim()
                    if job is None:
                        if self.process is not None and time.monotonic() - last_used >= IDLE_SECONDS:
                            self.unload()
                        self.stop_event.wait(.7)
                        continue
                    key = job["id"]
                    paused = False
                    try:
                        self.run(job)
                    except WorkerStopping:
                        paused = True
                        update(key, status="queued", phase="Waiting to resume", queue_priority=2)
                    except Cancelled:
                        self.unload()
                        update(key, status="cancelled", phase="Cancelled")
                    except Exception as exc:
                        log.exception("Synthesis failed for %s", key)
                        self.unload()
                        update(key, status="failed", phase="Failed", error=str(exc)[:700])
                    finally:
                        folder = JOBS / key
                        scratch_files = ["chunk.wav", "assembly.wav", "export.wav", "export.mp3", "export.flac"]
                        if not paused:
                            scratch_files.extend(("assembly.pcm", "assembly.json", "assembly.tmp"))
                        for scratch in scratch_files:
                            (folder / scratch).unlink(missing_ok=True)
                        last_used = time.monotonic()
            finally:
                self.unload()
