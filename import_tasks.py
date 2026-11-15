import concurrent.futures
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from documents import IMPORT_EXTENSIONS, MAX_BYTES
from storage import DATA, ROOT


class ImportTasks:
    """One parser, at most three waiting documents, receipts expire after 30 minutes."""
    def __init__(self):
        self.root = DATA / "imports"
        self.root.mkdir(parents=True, exist_ok=True)
        # Receipts cannot survive a service restart; remove only our own abandoned work.
        for folder in self.root.glob("doc-*"):
            if folder.is_dir() and not folder.is_symlink():
                shutil.rmtree(folder, ignore_errors=True)
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="document-import")
        self.lock = threading.Lock()
        self.tasks = {}
        self.reserved = 0
        self.closed = threading.Event()

    def cleanup(self):
        now = time.monotonic()
        expired = [key for key, task in self.tasks.items() if task["future"].done() and now - task["created"] > 1800]
        for key in expired:
            shutil.rmtree(self.tasks.pop(key)["folder"], ignore_errors=True)

    def sweep(self):
        with self.lock:
            self.cleanup()

    def submit(self, upload, owner):
        extension = Path(upload.filename).suffix.lower()
        if extension not in IMPORT_EXTENSIONS:
            raise ValueError("Choose a supported file such as PDF, DOCX, EPUB, MOBI, or TXT.")
        if not upload.size or upload.size > MAX_BYTES:
            raise ValueError("Choose a nonempty document up to 65 MB.")
        with self.lock:
            self.cleanup()
            if self.closed.is_set() or self.reserved + sum(not task["future"].done() for task in self.tasks.values()) >= 4:
                raise ValueError("Document import is busy. Wait for the current imports and try again.")
            self.reserved += 1
        folder = None
        try:
            if shutil.disk_usage(DATA).free < 1024 ** 3:
                raise ValueError("The server needs at least 1 GiB of free disk space to import a document.")
            folder = Path(tempfile.mkdtemp(prefix="doc-", dir=self.root))
            source = folder / ("source" + extension)
            upload.file.seek(0)
            with source.open("wb") as dest:
                shutil.copyfileobj(upload.file, dest, 1024 * 1024)
            key = uuid.uuid4().hex
            with self.lock:
                future = self.executor.submit(self.parse, source, folder / "result.json")
                self.tasks[key] = {"future": future, "owner": owner, "folder": folder, "created": time.monotonic()}
            return key
        except BaseException:
            if folder:
                shutil.rmtree(folder, ignore_errors=True)
            raise
        finally:
            with self.lock:
                self.reserved -= 1

    def parse(self, source, result):
        process = None
        try:
            if self.closed.is_set():
                return {"detail": "The server restarted during import. Please upload the file again."}
            process = subprocess.Popen([sys.executable, str(ROOT / "import_worker.py"), str(source), str(result)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                                       env={**os.environ, "TMPDIR": str(source.parent)})
            deadline = time.monotonic() + 90
            while process.poll() is None:
                if self.closed.wait(.1) or time.monotonic() > deadline:
                    return {"detail": "Document import stopped or exceeded 90 seconds. Try a smaller document."}
                if shutil.disk_usage(source.parent).free < 1024 ** 3:
                    return {"detail": "Document import stopped because the server is low on free disk space."}
            if process.returncode or not result.is_file() or result.stat().st_size > 40 * 1024 ** 2:
                return {"detail": "The document is damaged or exceeds the parser’s resource limits."}
            return {"ready": True}
        except OSError:
            return {"detail": "The document parser could not start. Please try again."}
        finally:
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            for item in source.parent.iterdir():
                if item == result:
                    continue
                if item.is_dir() and not item.is_symlink():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    try:
                        item.unlink(missing_ok=True)
                    except OSError:
                        pass

    def result(self, key, owner):
        with self.lock:
            self.cleanup()
            task = self.tasks.get(key)
            if not task or task["owner"] != owner:
                return {"detail": "This import expired or the server restarted. Please upload it again."}, 404
            future = task["future"]
            if not future.done():
                return {"import_id": key, "status": "Reading document" if future.running() else "Waiting to read document"}, 202
            result = future.result()
            if result.get("ready"):
                try:
                    result = json.loads((task["folder"] / "result.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    result = {"detail": "The import receipt is unavailable. Please upload the document again."}
            return result, 400 if "detail" in result else 200

    def close(self):
        self.closed.set()
        self.executor.shutdown(wait=True, cancel_futures=True)
        for task in self.tasks.values():
            shutil.rmtree(task["folder"], ignore_errors=True)
