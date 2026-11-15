"""Parse untrusted documents in a bounded child process, never the web process."""
import json
import resource
import sys
from pathlib import Path
from types import SimpleNamespace


def main():
    resource.setrlimit(resource.RLIMIT_AS, (1024 ** 3, 1024 ** 3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 ** 2, 256 * 1024 ** 2))
    resource.setrlimit(resource.RLIMIT_CPU, (80, 80))
    from documents import import_document
    path = Path(sys.argv[1])
    try:
        with path.open("rb") as file:
            result = import_document(SimpleNamespace(filename=path.name, file=file, size=path.stat().st_size))
    except ValueError as exc:
        result = {"detail": str(exc)}
    except Exception:
        result = {"detail": "Could not read this document. Check that it opens and is unlocked."}
    # A separate file prevents parser diagnostics on stdout from corrupting the receipt.
    Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
