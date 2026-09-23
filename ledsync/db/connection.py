"""SQLite connection and initialisation."""

import logging
import sqlite3
from pathlib import Path

from .schema import DDL, SCHEMA_VERSION, TABLES

BUSY_TIMEOUT_MS = 5000


class SchemaError(RuntimeError):
    pass


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection. SQLite connections must not be shared across threads
    (the UI server is threaded) - every thread/request opens its own.

    WAL + a busy timeout let a scheduled run and the UI (Phase 12) overlap without
    "database is locked" failures. The DB must live on a local disk (WAL does not
    work over network shares)."""
    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path) -> None:
    """Create the database and all tables if absent. Idempotent and never
    drops or alters existing data (BRD Section 28.2 / Business Rule 9)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise SchemaError(
                f"This database was created by a newer version of the application "
                f"(schema {version}; this version supports {SCHEMA_VERSION}). "
                "Install the latest application version."
            )
        conn.executescript(DDL)
        _ensure_event_id_index(conn)
        _ensure_events_cutoff_columns(conn)
        if version == 0:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        _verify(conn)
    finally:
        conn.close()


def _ensure_event_id_index(conn: sqlite3.Connection) -> None:
    """Event IDs are case-insensitive: 'abc' and 'ABC' must never be two events (Windows
    folders would collide). Idempotent. If an existing database somehow already holds two
    IDs differing only by case the index cannot be built - the application still starts,
    the registration code enforces the rule, and the problem is logged for the operator."""
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_events_event_id_nocase "
                     "ON events (event_id COLLATE NOCASE)")
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        logging.getLogger("ledsync.db").warning(
            "events already holds Event IDs that differ only by case; the uniqueness index was not created.")


def _ensure_events_cutoff_columns(conn: sqlite3.Connection) -> None:
    """A database created before Phase 10 has neither `events.cutoff_enabled` nor `events.cutoff_time`
    (`CREATE TABLE IF NOT EXISTS` never alters an existing table). Added at the end, in the same order as a
    fresh database's DDL, so `_verify`'s column-order check passes either way. Idempotent."""
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    if "cutoff_enabled" not in columns:
        conn.execute("ALTER TABLE events ADD COLUMN cutoff_enabled INTEGER NOT NULL DEFAULT 0")
    if "cutoff_time" not in columns:
        conn.execute("ALTER TABLE events ADD COLUMN cutoff_time TEXT")


def _verify(conn: sqlite3.Connection) -> None:
    for table, expected in TABLES.items():
        actual = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
        if actual != expected:
            raise SchemaError(f"Table '{table}' has unexpected columns: {actual}")
