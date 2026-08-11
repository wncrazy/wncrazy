import json
import sqlite3
import threading
from contextlib import contextmanager

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    filename TEXT NOT NULL,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    width INTEGER,
    height INTEGER,
    taken_at TEXT,
    camera TEXT,
    phash TEXT,
    sharpness_score REAL,
    exposure_score REAL,
    is_blurry INTEGER DEFAULT 0,
    ai_score REAL,
    ai_reason TEXT,
    ai_flags TEXT,
    combined_score REAL,
    duplicate_group_id INTEGER,
    is_best_in_group INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    decision TEXT DEFAULT 'unset',
    error TEXT,
    updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_photos_group ON photos(duplicate_group_id);
CREATE INDEX IF NOT EXISTS idx_photos_status ON photos(status);

CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    folder TEXT NOT NULL,
    started_at REAL,
    finished_at REAL,
    total INTEGER DEFAULT 0,
    processed INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running',
    error TEXT
);
"""


def get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn"):
        conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _local.conn = conn
    return _local.conn


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def tx():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in ("ai_flags",):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except (TypeError, ValueError):
                pass
    return d
