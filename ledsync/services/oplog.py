"""Operational log writer (BRD Section 25) - minimal in Phase 1, extended in Phase 9.

Timestamps are stored as ISO-8601 UTC text (`2026-09-20T17:07:22Z`); the display
format DD/MM/YY HH:MM:SS (BRD Section 25) is applied when the log is shown.
NOTE: this fixes UTC for *this log only*; the separate open decision on
local-time-vs-UTC for change-log comparison (BRD Section 36) is not made here.

NEVER put passwords, keys or other credentials in `message`.

Writes are best-effort: a failure to write an audit row must not turn a login (or
any operation) into an error page. The failure goes to the diagnostic log instead.
"""

import logging
import sqlite3
from datetime import datetime, timezone

log = logging.getLogger("ledsync.oplog")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add(
    conn: sqlite3.Connection,
    operation: str,
    status: str,
    message: str = "",
    event_id: str | None = None,
) -> None:
    """Insert a row WITHOUT committing and WITHOUT swallowing errors, so a state-changing
    operation can write its data and its audit row in ONE transaction (both or neither)."""
    conn.execute(
        "INSERT INTO operation_log (event_id, operation, timestamp, status, message) "
        "VALUES (?, ?, ?, ?, ?)",
        (event_id, operation, _now(), status, message),
    )


def record(
    conn: sqlite3.Connection,
    operation: str,
    status: str,
    message: str = "",
    event_id: str | None = None,
) -> None:
    try:
        add(conn, operation, status, message, event_id)
        conn.commit()
    except sqlite3.Error:
        log.exception("Could not write operation_log row (%s / %s)", operation, status)


# --- reviewing (Phase 9) ---------------------------------------------------------------------------------------------------

PAGE_SIZE = 100

# The operation names written by the application (BRD Section 25). The two marked (later) are written by Phases 12 and 13.
OPERATIONS = (
    "Application Startup", "Login", "Logout", "Password Change", "Event Registered", "Event Re-registered", "Settings Changed",
    "Mapping Saved", "Device Test", "Cloud Storage Test", "Check Changes", "Download & Sync", "Download Files", "Sync Files",
    "Asset Download", "RPI Files", "Synchronise", "Exception Reviewed", "Log Export", "Email Notification",
    "Scheduled Run",          # Phase 12
    "Application Update",     # (later) Phase 13
)
STATUS_FILTERS = ("Success", "Failed", "Blocked", "Started", "Cancelled", "Skipped")


def _like(text: str) -> str:
    """A LIKE pattern that matches `text` literally (! is the escape character)."""
    return "%" + text.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"


def query(conn: sqlite3.Connection, *, event_id: str = "", operation: str = "", status: str = "", start: str = "",
          end: str = "", text: str = "", limit: int = PAGE_SIZE, offset: int = 0):
    """(rows, total) newest first. `start` / `end` are UTC timestamps (start inclusive, end exclusive)."""
    where, args = [], []
    if event_id:
        where.append("event_id = ? COLLATE NOCASE")
        args.append(event_id)
    if operation:
        where.append("operation = ? COLLATE NOCASE")
        args.append(operation)
    if status in STATUS_FILTERS:
        where.append("status = ?")
        args.append(status)
    if start:
        where.append("timestamp >= ?")
        args.append(start)
    if end:
        where.append("timestamp < ?")
        args.append(end)
    if text:
        where.append("message LIKE ? ESCAPE '!'")
        args.append(_like(text))
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute("SELECT COUNT(*) FROM operation_log" + clause, args).fetchone()[0]
    rows = conn.execute("SELECT * FROM operation_log" + clause + " ORDER BY log_id DESC LIMIT ? OFFSET ?",
                        [*args, max(1, min(int(limit), 100000)), max(0, min(int(offset), 10**15))]).fetchall()
    return rows, total


def prune(conn: sqlite3.Connection, before_iso: str) -> int:
    """Delete operational-log rows older than `before_iso` (a UTC ISO timestamp). Returns the number removed.
    Never raises outward - a failed prune must not stop the application from starting."""
    try:
        n = conn.execute("DELETE FROM operation_log WHERE timestamp < ?", (before_iso,)).rowcount
        conn.commit()
        return n
    except sqlite3.Error:
        conn.rollback()
        log.exception("Could not prune the operational log")
        return 0
