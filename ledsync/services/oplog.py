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


def record(
    conn: sqlite3.Connection,
    operation: str,
    status: str,
    message: str = "",
    event_id: str | None = None,
) -> None:
    try:
        conn.execute(
            "INSERT INTO operation_log (event_id, operation, timestamp, status, message) "
            "VALUES (?, ?, ?, ?, ?)",
            (event_id, operation, _now(), status, message),
        )
        conn.commit()
    except sqlite3.Error:
        log.exception("Could not write operation_log row (%s / %s)", operation, status)
