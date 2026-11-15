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
import wave
import unicodedata
from collections import deque
from contextlib import ExitStack
from pathlib import Path

from generation_limits import SpeechLimitError
from storage import AISPEECH, DATA, JOBS, ROOT, connect, one


log = logging.getLogger(__name__)
MODEL_RUNTIME = {"qwen_base": "qwen", "higgs_bf16": "index", "higgs_q8": "index", "chatterbox": "lahgtna"}
MIN_DISK_FREE = 1024 ** 3
IDLE_SECONDS = float(os.getenv("TTS_MODEL_IDLE_SECONDS", "120"))


class Cancelled(Exception):
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
    rest = text.strip()
    while len(rest) > limit:
        search = rest[:limit + 1]
        ends = list(SENTENCE_END.finditer(search))
        cut = ends[-1].end() if ends and ends[-1].end() >= limit // 3 else search.rfind(" ")
        cut = safe_cut(rest, cut if cut > 0 else limit)
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
        if self.stop_event.is_set() or one("SELECT cancel FROM jobs WHERE id=?", (job,))["cancel"]:
            raise Cancelled

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
                    raise RuntimeError("The model process exited. Check worker.log for the error.")
                self.output.extend(data)
        raise RuntimeError("The model timed out. Try a shorter chunk or check worker.log.")

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
        chunks = deque((part, 0) for part in chunk_text(script, settings["chunk_size"]))
        if not chunks:
            raise ValueError("The text has no spoken content after removing tags.")
        if shutil.disk_usage(folder).free < MIN_DISK_FREE:
            raise RuntimeError("Free at least 1 GiB of server disk space before generating audio.")
        self.load(job["model"], key)
        total = len(chunks)
        update(key, phase="Preparing voice", total=total)
        reference = folder / "reference.wav"
        prepared = self.send(key, {"op": "prepare", "script": script, "settings": settings,
                                   "reference": str(reference) if reference.exists() else None,
                                   "emotion": str(folder / "emotion.wav") if settings.get("emo_voice") else None})
        if prepared.get("dialect") and settings["dialect"] == "auto":
            settings["detected_dialect"] = prepared["dialect"]
            update(key, settings=json.dumps(settings))
        update(key, phase="Generating speech")
        with ExitStack() as files:
            output = None
            done = 0
            while chunks:
                text, depth = chunks.popleft()
                if shutil.disk_usage(folder).free < MIN_DISK_FREE:
                    raise RuntimeError("Generation stopped because the server has less than 1 GiB of free disk space.")
                try:
                    self.send(key, {"op": "render", "text": text, "output": str(part), "first": done == 0,
                                    "settings": settings, **prepared})
                except SpeechLimitError as exc:
                    left, right = split_retry(text)
                    if depth >= 5 or min(len(left), len(right)) < 8:
                        raise SpeechLimitError(f"Chunk {done + 1} still reached the audio-token limit after shortening. "
                                               "Raise the audio-token limit in Advanced generation if available.") from exc
                    chunks.extendleft(((right, depth + 1), (left, depth + 1)))
                    total += 1
                    update(key, total=total)
                    continue
                with wave.open(str(part), "rb") as audio:
                    if output is None:
                        rate = audio.getframerate()
                        output = files.enter_context(wave.open(str(source), "wb"))
                        output.setparams(audio.getparams())
                        silence = b"\0" * round(rate * settings["pause_ms"] / 1000) * audio.getsampwidth() * audio.getnchannels()
                    elif done:
                        output.writeframesraw(silence)
                    output.writeframesraw(audio.readframes(audio.getnframes()))
                done += 1
                update(key, done=done)
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
