"""Exception log writer (BRD Section 21.1) - minimal in Phase 3, extended in Phase 9.

One row per error, with the BRD 21 category, the operation that failed, and where
relevant the event / table / LED type / file / source / destination.

NEVER put passwords, keys, or the contents of untrusted files in `message`. Field
names and reasons only. Writes are best-effort: failing to write an exception row
must not turn a handled error into a crash - it goes to the diagnostic log instead.
"""

import logging
import sqlite3
from datetime import datetime, timezone

log = logging.getLogger("ledsync.exceptions")

# BRD Section 21: the categories the application must distinguish.
DOWNLOAD = "Download"
STORAGE_CONNECTIVITY = "Azure Storage connectivity"
GUID_VALIDATION = "GUID validation"
CONFIGURATION = "Configuration"
FILE_ACCESS = "File access"
NETWORK_DEVICE = "Network/device connectivity"
SYNCHRONISATION = "Synchronisation"
PERMISSION = "Permission"
MISSING_FOLDER = "Missing folder"
INVALID_CONFIGURATION = "Invalid configuration"

CATEGORIES = (
    DOWNLOAD, STORAGE_CONNECTIVITY, GUID_VALIDATION, CONFIGURATION, FILE_ACCESS,
    NETWORK_DEVICE, SYNCHRONISATION, PERMISSION, MISSING_FOLDER, INVALID_CONFIGURATION,
)

OPEN = "Open"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record(
    conn: sqlite3.Connection,
    category: str,
    operation: str,
    message: str,
    *,
    event_id: str | None = None,
    table_number: int | None = None,
    led_type: str | None = None,
    file_name: str | None = None,
    source: str | None = None,
    destination: str | None = None,
    resolution_status: str = OPEN,
) -> None:
    if category not in CATEGORIES:
        raise ValueError(f"Unknown exception category: {category!r}")
    try:
        conn.execute(
            "INSERT INTO exception_log (event_id, timestamp, operation, category, message, "
            "resolution_status, table_number, led_type, file_name, source, destination) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, _now(), operation, category, message, resolution_status,
             table_number, led_type, file_name, source, destination),
        )
        conn.commit()
    except sqlite3.Error:
        log.exception("Could not write exception_log row (%s / %s)", category, operation)


# --- reviewing, resolving and querying (Phase 9) ---------------------------------------------------------------------------

ACKNOWLEDGED, RESOLVED = "Acknowledged", "Resolved"
STATUSES = (OPEN, ACKNOWLEDGED, RESOLVED)
PAGE_SIZE = 100


def resolve_matching(conn: sqlite3.Connection, event_id: str | None, operation: str, *, table_number: int | None = None,
                     led_type: str | None = None, file_name: str | None = None, source: str | None = None,
                     destination: str | None = None) -> int:
    """Mark earlier exceptions of exactly the same kind as Resolved because the same operation has now succeeded: same event,
    operation, table, LED type, file, source and destination (a field not given must be empty in the row). It does NOT commit
    (the caller's transaction holds the success) and never raises. Returns the number of rows changed."""
    try:
        clauses = ["resolution_status IN (?, ?)", "operation = ? COLLATE NOCASE"]
        args: list = [OPEN, ACKNOWLEDGED, operation]
        for column, value in (("event_id", event_id), ("table_number", table_number), ("led_type", led_type),
                              ("file_name", file_name), ("source", source), ("destination", destination)):
            if value is None:
                clauses.append(f"{column} IS NULL")
            else:
                clauses.append(f"{column} = ? COLLATE NOCASE" if isinstance(value, str) else f"{column} = ?")
                args.append(value)
        return conn.execute("UPDATE exception_log SET resolution_status = ? WHERE " + " AND ".join(clauses),
                            [RESOLVED, *args]).rowcount
    except sqlite3.Error:
        log.exception("Could not resolve earlier exceptions (%s)", operation)
        return 0


def set_status(conn: sqlite3.Connection, exception_id: int, status: str) -> bool:
    """The administrator's review of one exception. Returns False if it does not exist. Audited in the operational log."""
    from . import oplog
    if status not in STATUSES:
        raise ValueError(f"Unknown resolution status: {status!r}")
    row = conn.execute("SELECT event_id, category, resolution_status FROM exception_log WHERE exception_id = ?",
                       (exception_id,)).fetchone()
    if row is None:
        return False
    try:
        conn.execute("UPDATE exception_log SET resolution_status = ? WHERE exception_id = ?", (status, exception_id))
        oplog.add(conn, "Exception Reviewed", "Success",
                  f"Exception {exception_id} ({row['category']}) changed from {row['resolution_status']} to {status}.", row["event_id"])
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        log.exception("Could not change the status of exception %s", exception_id)
        return False
    return True


def _like(text: str) -> str:
    """A LIKE pattern that matches `text` literally (! is the escape character)."""
    return "%" + text.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"


def query(conn: sqlite3.Connection, *, event_id: str = "", category: str = "", status: str = "", start: str = "",
          end: str = "", text: str = "", limit: int = PAGE_SIZE, offset: int = 0):
    """(rows, total) newest first. `start` / `end` are UTC timestamps (start inclusive, end exclusive)."""
    where, args = [], []
    if event_id:
        where.append("event_id = ? COLLATE NOCASE")
        args.append(event_id)
    if category in CATEGORIES:
        where.append("category = ?")
        args.append(category)
    if status in STATUSES:
        where.append("resolution_status = ?")
        args.append(status)
    if start:
        where.append("timestamp >= ?")
        args.append(start)
    if end:
        where.append("timestamp < ?")
        args.append(end)
    if text:
        where.append("(message LIKE ? ESCAPE '!' OR file_name LIKE ? ESCAPE '!' OR destination LIKE ? ESCAPE '!')")
        args += [_like(text)] * 3
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute("SELECT COUNT(*) FROM exception_log" + clause, args).fetchone()[0]
    rows = conn.execute("SELECT * FROM exception_log" + clause + " ORDER BY exception_id DESC LIMIT ? OFFSET ?",
                        [*args, max(1, min(int(limit), 100000)), max(0, min(int(offset), 10**15))]).fetchall()
    return rows, total


def record_rejection(conn: sqlite3.Connection, oplog_operation: str, operation: str, message: str, *,
                     event_id: str | None = None, category: str = "Invalid configuration") -> None:
    """A change the administrator tried to save was refused: one exception row (default category Invalid configuration) and
    one Failed operational-log row. `message` is the fixed plain-text reason, never a typed value. Never raises."""
    import re
    from . import oplog
    # Anything typed between quotes (a value the administrator entered, possibly a pasted key) is never stored.
    message = re.sub(r"'[^']*'", "'...'", message)[:300]
    record(conn, category, operation, message, event_id=event_id)
    oplog.record(conn, oplog_operation, "Failed", message, event_id)


def prune(conn: sqlite3.Connection, before_iso: str) -> int:
    """Delete exception-log rows older than `before_iso` (a UTC ISO timestamp). Returns the number removed.
    Never raises outward - a failed prune must not stop the application from starting."""
    try:
        n = conn.execute("DELETE FROM exception_log WHERE timestamp < ?", (before_iso,)).rowcount
        conn.commit()
        return n
    except sqlite3.Error:
        conn.rollback()
        log.exception("Could not prune the exception log")
        return 0
