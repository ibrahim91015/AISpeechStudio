import fcntl
import json
import logging
import os
import re
import select
import subprocess
import threading
import time
import wave
import unicodedata

from storage import AISPEECH, DATA, JOBS, ROOT, connect, one


log = logging.getLogger(__name__)
IDLE_SECONDS = float(os.getenv("TTS_MODEL_IDLE_SECONDS", "75"))


class Cancelled(Exception):
    pass


def chunk_text(text, limit):
    rest = text.strip()
    while len(rest) > limit:
        search = rest[:limit + 1]
        ends = list(re.finditer(r"[.!?؟؛。！？](?=\s|$)|\n\n", search))
        cut = ends[-1].end() if ends and ends[-1].end() >= limit // 3 else search.rfind(" ")
        if cut <= 0:
            cut = limit
            while cut > 1 and unicodedata.combining(rest[cut]):
                cut -= 1
        yield rest[:cut].strip()
        rest = rest[cut:].lstrip()
    if rest:
        yield rest


def update(job, **changes):
    with connect() as db:
        names = ",".join(f"{key}=?" for key in changes)
        db.execute(f"UPDATE jobs SET {names}, updated=CURRENT_TIMESTAMP WHERE id=?", (*changes.values(), job))


def claim():
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created,rowid LIMIT 1").fetchone()
        if row is None:
            return None
        db.execute("UPDATE jobs SET status='running', phase='Preparing', updated=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
        return dict(row)


class Worker:
    def __init__(self):
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.loop, name="tts-queue", daemon=True)
        self.process = None
        self.model = None

    def start(self):
        self.thread.start()

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=12)

    def publish(self):
        with connect() as db:
            db.execute("INSERT OR REPLACE INTO worker_state(singleton,model,last_seen) VALUES (1,?,?)", (self.model, time.time()))

    def unload(self):
        p, self.process = self.process, None
        self.model = None
        if p:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
            p.stdin.close()
            p.stdout.close()
        self.publish()

    def check(self, job):
        if self.stop_event.is_set() or one("SELECT cancel FROM jobs WHERE id=?", (job,))["cancel"]:
            raise Cancelled

    def receive(self, job, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check(job)
            if select.select([self.process.stdout], [], [], .4)[0]:
                line = self.process.stdout.readline()
                if not line:
                    raise RuntimeError("The model process exited. Check worker.log for the error.")
                result = json.loads(line)
                if "error" in result:
                    raise RuntimeError(result["error"])
                return result
        raise RuntimeError("The model timed out. Try a shorter chunk or check worker.log.")

    def load(self, model, job):
        if self.model == model and self.process.poll() is None:
            return
        self.unload()
        self.check(job)
        update(job, phase="Loading model")
        env = {**os.environ, "HF_HUB_CACHE": str(AISPEECH / "huggingface/hub"), "HF_HUB_OFFLINE": "1",
               "TOKENIZERS_PARALLELISM": "false", "OMP_NUM_THREADS": "2", "PYTHONUNBUFFERED": "1"}
        python = ROOT / ".runtime" / model / "bin/python"
        with (DATA / "worker.log").open("ab") as dest:
            self.process = subprocess.Popen(
                [str(python), str(ROOT / "model_worker.py"), model], cwd=ROOT, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=dest, text=True, encoding="utf-8", bufsize=1,
            )
        if not self.receive(job, 240).get("ready"):
            raise RuntimeError("The model process failed to start.")
        self.model = model
        self.publish()

    def send(self, job, payload, seconds=180):
        self.check(job)
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()
        return self.receive(job, seconds)["ok"]

    def run(self, job):
        key = job["id"]
        folder = JOBS / key
        folder.mkdir(exist_ok=True)
        part = folder / "chunk.wav"
        source = folder / "assembly.wav"
        settings = json.loads(job["settings"])
        script = job["script"]
        if job["model"] == "lahgtna" and not settings["keep_tags"]:
            script = re.sub(r"\[[^\]\n]{1,40}\]", "", script)
        chunks = list(chunk_text(script, settings["chunk_size"]))
        if not chunks:
            raise ValueError("The text has no spoken content after removing tags.")
        self.load(job["model"], key)
        update(key, phase="Preparing voice", total=len(chunks))
        reference = folder / "reference.wav"
        prepared = self.send(key, {"op": "prepare", "script": script, "settings": settings,
                                   "reference": str(reference) if reference.exists() else None})
        if prepared.get("dialect") and settings["dialect"] == "auto":
            settings["detected_dialect"] = prepared["dialect"]
            update(key, settings=json.dumps(settings))
        update(key, phase="Generating speech")
        with wave.open(str(source), "wb") as output:
            rate = None
            for index, text in enumerate(chunks):
                self.send(key, {"op": "render", "text": text, "output": str(part), "first": index == 0,
                                "settings": settings, **prepared})
                with wave.open(str(part), "rb") as audio:
                    if rate is None:
                        rate = audio.getframerate()
                        output.setparams(audio.getparams())
                        silence = b"\0" * round(rate * settings["pause_ms"] / 1000) * audio.getsampwidth() * audio.getnchannels()
                    elif index:
                        output.writeframesraw(silence)
                    output.writeframesraw(audio.readframes(audio.getnframes()))
                update(key, done=index + 1)
            duration = output.getnframes() / rate
        self.check(key)
        update(key, phase="Exporting audio")
        final = folder / f"speech.{settings['format']}"
        if settings["format"] == "wav" and not settings["normalize"]:
            source.replace(final)
        else:
            staged = folder / f"export.{settings['format']}"
            args = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source)]
            if settings["normalize"]:
                args += ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
            if settings["format"] == "mp3":
                args += ["-codec:a", "libmp3lame", "-q:a", "2"]
            subprocess.run([*args, "-ar", str(rate), str(staged)], check=True, timeout=240)
            self.check(key)
            staged.replace(final)
        update(key, status="finished", phase="Ready", duration=duration)

    def loop(self):
        with (DATA / "worker.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            with connect() as db:
                db.execute("UPDATE jobs SET status='failed', phase='Interrupted', error='Server restarted during synthesis. Reopen the job and queue it again.' WHERE status='running'")
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
                    try:
                        self.run(job)
                    except Cancelled:
                        self.unload()
                        update(key, status="cancelled", phase="Cancelled")
                    except Exception as exc:
                        log.exception("Synthesis failed for %s", key)
                        self.unload()
                        update(key, status="failed", phase="Failed", error=str(exc)[:700])
                    finally:
                        folder = JOBS / key
                        for scratch in ("chunk.wav", "assembly.wav", "export.wav", "export.mp3", "export.flac"):
                            (folder / scratch).unlink(missing_ok=True)
                        last_used = time.monotonic()
            finally:
                self.unload()
