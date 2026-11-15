"""Render a test page with a temporary database, without starting a GPU worker."""
import os
import sys
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="aispeech-dom-") as data:
    os.environ["TTS_DATA_DIR"] = data
    from storage import setup
    setup()
    from fastapi.testclient import TestClient
    from app import app
    response = TestClient(app).get("/")
    response.raise_for_status()
    Path(sys.argv[1]).write_text(response.text, encoding="utf-8")
