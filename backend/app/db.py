import sqlite3
from contextlib import contextmanager
from typing import Optional

from .config import get_settings
from .utils.filesystem import ensure_storage


SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
 id TEXT PRIMARY KEY, filename TEXT NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL,
 duration REAL, width INTEGER, height INTEGER, fps REAL, codec TEXT, audio_streams INTEGER,
 audio_codec TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS highlights (
 id TEXT PRIMARY KEY, video_id TEXT NOT NULL, start_time REAL NOT NULL, end_time REAL NOT NULL,
 score INTEGER NOT NULL, category TEXT NOT NULL, reason TEXT NOT NULL, transcript_excerpt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transcripts (
 id TEXT PRIMARY KEY, video_id TEXT NOT NULL UNIQUE, path TEXT NOT NULL, language TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clips (
 id TEXT PRIMARY KEY, video_id TEXT NOT NULL, highlight_id TEXT, start_time REAL NOT NULL, end_time REAL NOT NULL,
 output_path TEXT NOT NULL, thumbnail_path TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, video_id TEXT NOT NULL, type TEXT NOT NULL, status TEXT NOT NULL, progress INTEGER NOT NULL,
 current_step TEXT NOT NULL, error TEXT, cancelled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
 video_id TEXT PRIMARY KEY, has_transcript INTEGER NOT NULL, segments INTEGER NOT NULL,
 llm_provider TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS publications (
 id TEXT PRIMARY KEY, clip_id TEXT NOT NULL, provider TEXT NOT NULL,
 external_id TEXT NOT NULL, url TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_settings (
 key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clip_stats (
 clip_id TEXT PRIMARY KEY, views INTEGER NOT NULL DEFAULT 0,
 likes INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
"""


DB_FILENAME = "klipani.db"
LEGACY_DB_FILENAME = "clipper.db"


def initialize() -> None:
    settings = get_settings()
    ensure_storage(settings.storage)
    legacy = settings.storage / LEGACY_DB_FILENAME
    current = settings.storage / DB_FILENAME
    if not current.exists() and legacy.exists():
        legacy.rename(current)
    with connect() as connection:
        connection.executescript(SCHEMA)
        try:
            connection.execute("ALTER TABLE clips ADD COLUMN kind TEXT NOT NULL DEFAULT 'CUT'")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            connection.execute("ALTER TABLE jobs ADD COLUMN subtitles INTEGER NOT NULL DEFAULT 1")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            connection.execute("ALTER TABLE jobs ADD COLUMN style TEXT NOT NULL DEFAULT 'crop'")
        except sqlite3.OperationalError:
            pass  # column already exists


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(get_settings().storage / DB_FILENAME)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def database():
    connection = connect()
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def one(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return dict(row) if row else None


def many(rows) -> list[dict]:
    return [dict(row) for row in rows]
