import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parent
AISPEECH = ROOT.parent
DATA = Path(os.environ.get("TTS_DATA_DIR", ROOT / "data"))
JOBS = DATA / "jobs"
VOICES = DATA / "voices"
REFERENCES = DATA / "references"
DB = DATA / "studio.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS voices (
    id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), name TEXT NOT NULL,
    created TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), name TEXT NOT NULL,
    created TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id, name)
);
CREATE TABLE IF NOT EXISTS presets (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), name TEXT NOT NULL,
    model TEXT NOT NULL, settings TEXT NOT NULL, UNIQUE(user_id, name)
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    model TEXT NOT NULL, title TEXT NOT NULL, script TEXT NOT NULL, settings TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued','running','finished','failed','cancelled')),
    phase TEXT NOT NULL DEFAULT 'Waiting for GPU', done INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0, cancel INTEGER NOT NULL DEFAULT 0,
    error TEXT, duration REAL, created TEXT DEFAULT CURRENT_TIMESTAMP,
    updated TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, created);
CREATE INDEX IF NOT EXISTS user_history ON jobs(user_id, created DESC);
CREATE UNIQUE INDEX IF NOT EXISTS one_running_job ON jobs(status) WHERE status='running';
CREATE TABLE IF NOT EXISTS worker_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton=1), model TEXT, last_seen REAL NOT NULL
);
INSERT OR IGNORE INTO users(id,name) VALUES (1,'Studio');
"""


@contextmanager
def connect():
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def setup():
    for folder in (DATA, JOBS, VOICES, REFERENCES):
        folder.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(SCHEMA)
        columns = {"voices": {"original_name": "TEXT", "media_type": "TEXT", "size": "INTEGER", "duration": "REAL"},
                   "worker_state": {"phase": "TEXT", "step": "INTEGER", "total": "INTEGER", "target": "TEXT"}}
        for table, fields in columns.items():
            existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
            for name, field_type in fields.items():
                if name not in existing:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {field_type}")


def rows(sql, args=()):
    with connect() as db:
        return [dict(row) for row in db.execute(sql, args)]


def one(sql, args=()):
    with connect() as db:
        row = db.execute(sql, args).fetchone()
    return dict(row) if row is not None else None
