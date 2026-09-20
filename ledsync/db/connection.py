"""SQLite connection and initialisation."""

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
        if version == 0:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        _verify(conn)
    finally:
        conn.close()


def _verify(conn: sqlite3.Connection) -> None:
    for table, expected in TABLES.items():
        actual = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
        if actual != expected:
            raise SchemaError(f"Table '{table}' has unexpected columns: {actual}")
