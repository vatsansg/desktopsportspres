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
