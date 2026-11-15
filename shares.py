import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import monotonic, time
from uuid import uuid4

from notification_events import enqueue
from storage import JOBS, connect


BITRATES = {32: "Smallest", 48: "Compact", 64: "Balanced", 96: "Clearer"}
TTL = 86_400
LONG_CONVERSION_SECONDS = 10
lock = Lock()
progress = {}
log = logging.getLogger(__name__)


def path_for(job, rate):
    return JOBS / job / f"share-{rate}.mp3"


def available(path):
    try:
        return path.stat().st_mtime > time() - TTL
    except FileNotFoundError:
        return False


def options(source, duration):
    original_bytes = source.stat().st_size
    return [{"rate": rate, "name": label, "bytes": int(duration * rate * 125 + 1024)}
            for rate, label in BITRATES.items() if duration * rate * 125 + 1024 < original_bytes]


def cleanup():
    for path in JOBS.glob("*/share-*.mp3"):
        if not available(path):
            path.unlink(missing_ok=True)


def status(job, rate):
    target = path_for(job, rate)
    if available(target):
        return {"state": "ready", "percent": 100, "bytes": target.stat().st_size}
    with lock:
        return progress.get((job, rate), {"state": "idle", "percent": 0})


def convert(job, rate, source, queued_at):
    identifier = job["id"]
    duration = job["duration"]
    key = (identifier, rate)
    target = path_for(*key)
    staged = target.with_suffix(".temp")
    ready_bytes = 0
    try:
        with subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-y", "-progress", "pipe:1",
                               "-i", str(source), "-map", "0:a:0", "-map_metadata", "-1", "-ac", "1",
                               "-codec:a", "libmp3lame", "-b:a", f"{rate}k", "-f", "mp3", str(staged)],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True) as process:
            for line in process.stdout:
                if line.startswith("out_time_us=") and (value := line.partition("=")[2].strip()).isdigit():
                    with lock:
                        progress[key] = {"state": "converting", "percent": min(99, int(int(value) / duration / 10_000))}
            if process.wait() or not staged.is_file() or staged.stat().st_size >= source.stat().st_size:
                raise ValueError("Recompression did not produce a smaller MP3.")
        size = staged.stat().st_size
        staged.replace(target)
        ready_bytes = size
    except (OSError, ValueError):
        log.exception("Share MP3 failed for %s at %s kbps", identifier, rate)
        with lock:
            progress[key] = {"state": "failed", "percent": 0}
    else:
        with lock:
            progress.pop(key, None)
    finally:
        staged.unlink(missing_ok=True)
    if monotonic() - queued_at >= LONG_CONVERSION_SECONDS:
        title = job["title"][:115]
        body = (f"{title} · {rate} kbps · {ready_bytes / 1_000_000:.2f} MB."
                if ready_bytes else f"{title} · {rate} kbps · Open Library to try another size.")
        url = f"/library?recording={identifier}&share={rate}#recording-{identifier}"
        with connect() as db:
            enqueue(db, job["user_id"], f"mp3-{identifier}-{rate}-{uuid4().hex[:8]}",
                    "MP3 ready" if ready_bytes else "MP3 conversion stopped", body, url, ping=bool(ready_bytes), ttl=3600)


def start(executor: ThreadPoolExecutor, job, rate, source):
    identifier = job["id"]
    if available(path_for(identifier, rate)):
        return status(identifier, rate)
    with lock:
        key = (identifier, rate)
        if progress.get(key, {}).get("state") not in {"queued", "converting"}:
            progress[key] = {"state": "queued", "percent": 0}
            executor.submit(convert, job, rate, source, monotonic())
        return progress[key]
