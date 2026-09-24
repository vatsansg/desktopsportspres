"""SQLite connection and initialisation."""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .migrations import run_migrations
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
        _ensure_event_guid_index(conn)
        if version == 0:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")   # brand-new: no migration to run
        elif version < SCHEMA_VERSION:
            run_migrations(conn, version, SCHEMA_VERSION)
            _record_schema_upgrade(conn, version, SCHEMA_VERSION)
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


def _record_schema_upgrade(conn: sqlite3.Connection, old_version: int, new_version: int) -> None:
    """BRD Section 25's "Application Update" operation (services/oplog.py's OPERATIONS already
    reserved this name for Phase 13) - written here with a raw INSERT, not services/oplog.add(),
    to avoid db/ reaching up into services/ for a layering violation over one row. Best-effort:
    an audit-write failure must never turn a successful schema upgrade into a startup failure -
    found missing entirely by the Phase 13 pre-hand-off review (a real upgrade left no
    operational-log trace at all that it had happened)."""
    try:
        conn.execute(
            "INSERT INTO operation_log (event_id, operation, timestamp, status, message) VALUES (?, ?, ?, ?, ?)",
            (None, "Application Update", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "Success",
             f"Database schema upgraded from version {old_version} to {new_version}."),
        )
    except sqlite3.Error:
        logging.getLogger("ledsync.db").exception(
            "Could not write the Application Update operation_log row for a schema upgrade %s -> %s.",
            old_version, new_version)


def _ensure_event_guid_index(conn: sqlite3.Connection) -> None:
    """A real database-level backstop for GUID uniqueness (Phase 3's own carried-forward
    hardening item, closed by the Phase 13 pre-hand-off review): previously enforced only at the
    application layer (services/registration.py's SELECT-then-INSERT, a TOCTOU gap mitigated in
    practice by the single-instance lock but with no database constraint behind it). Idempotent,
    same graceful-degradation pattern as _ensure_event_id_index: a pre-existing database that
    somehow already holds two GUIDs differing only by case simply does not get the index - the
    application still starts, registration.py's own existing check keeps enforcing the rule, and
    the problem is logged for the operator. NULLs (an event with no GUID) never conflict with
    each other, matching normal SQL UNIQUE semantics."""
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_events_event_guid_nocase "
                     "ON events (event_guid COLLATE NOCASE) WHERE event_guid IS NOT NULL")
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        logging.getLogger("ledsync.db").warning(
            "events already holds two GUIDs that differ only by case; the uniqueness index was not created.")


def _verify(conn: sqlite3.Connection) -> None:
    for table, expected in TABLES.items():
        actual = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
        if actual != expected:
            raise SchemaError(f"Table '{table}' has unexpected columns: {actual}")
