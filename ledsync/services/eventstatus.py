"""The event status shown on the Dashboard, computed from real state (owner decision 21/09/26).

    Attention needed  any unresolved failure: a download that failed and was never followed by a success, a device folder
                      whose latest push failed, or a mapped device whose last connection test failed
    Synced            nothing failed, at least one file has been downloaded, and nothing is waiting to be pushed
    Ready             nothing failed, every enabled LED is mapped and its last test passed, but nothing has been synced yet
    Registered        anything else (not fully mapped or not yet tested)

`refresh` is cheap (database only) and is called after mapping changes, device tests and every download / sync run.
"""

import sqlite3

from . import mappings, structure
STATUS_REGISTERED, STATUS_READY, STATUS_SYNCED, STATUS_ATTENTION = "Registered", "Ready", "Synced", "Attention needed"


def _unresolved_download_failures(conn: sqlite3.Connection, event_id: str) -> int:
    latest: dict = {}
    for r in conn.execute("SELECT table_number, led_type, file_name, status FROM download_history "
                          "WHERE event_id = ? COLLATE NOCASE ORDER BY download_id", (event_id,)):
        led = (r["led_type"] or "").casefold()
        name = (r["file_name"] or "").translate(_ASCII_LOWER)
        latest[(r["table_number"], led, name)] = r["status"]
    return sum(1 for status in latest.values() if status == "Failure")


_ASCII_LOWER = {ord(c): ord(c) + 32 for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}


def _failed_push_destinations(conn: sqlite3.Connection, event_id: str) -> int:
    import os
    latest: dict = {}
    for r in conn.execute("SELECT destination, status FROM sync_history WHERE event_id = ? COLLATE NOCASE ORDER BY sync_id",
                          (event_id,)):
        latest[os.path.normcase(r["destination"] or "")] = r["status"]
    return sum(1 for status in latest.values() if status == "Failure")


def compute(conn: sqlite3.Connection, event_id: str) -> str:
    from . import sync                                   # local import: sync imports this module's callers
    enabled = mappings.list_mappings(conn, event_id)
    if (_unresolved_download_failures(conn, event_id) or _failed_push_destinations(conn, event_id)
            or any(m.status == mappings.STATUS_FAILED for m in enabled)):
        return STATUS_ATTENTION
    downloaded = conn.execute("SELECT 1 FROM download_history WHERE event_id = ? COLLATE NOCASE AND status = 'Success' LIMIT 1",
                              (event_id,)).fetchone() is not None
    if downloaded and not sync.plan(conn, event_id, None)[0]:
        return STATUS_SYNCED
    if enabled and all(m.shared_folder and m.status == mappings.STATUS_OK for m in enabled):
        return STATUS_READY
    return STATUS_REGISTERED


def refresh(conn: sqlite3.Connection, event_id: str) -> str:
    """Recompute and store the status. Never raises (a status must never break the action that triggered it)."""
    try:
        status = compute(conn, event_id)
        conn.execute("UPDATE events SET status = ? WHERE event_id = ? COLLATE NOCASE", (status, event_id))
        conn.commit()
        return status
    except sqlite3.Error:
        conn.rollback()
        return ""
