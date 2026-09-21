"""The event status shown on the Dashboard, computed from real state (owner decision 21/09/26).

    Attention needed  any unresolved failure: a download that failed and was never followed by a success, a device folder
                      whose latest push failed, or a mapped device whose last connection test failed
    Synced            nothing failed, at least one file has been downloaded, and nothing is waiting to be pushed
    Ready             nothing failed, every enabled LED is mapped and its last test passed, but nothing has been synced yet
    Registered        anything else (not fully mapped or not yet tested)

`refresh` is cheap (database only) and is called after mapping changes, device tests and every download / sync run.
"""

import sqlite3

from . import mappings

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


def compute(conn: sqlite3.Connection, event_id: str) -> str:
    import os
    from . import sync                                   # local import: sync imports this module's callers
    enabled = mappings.list_mappings(conn, event_id)
    items, unmapped = sync.plan(conn, event_id, None)
    latest = sync.latest_outcomes(conn, event_id)
    # An unresolved push or removal failure is one that still needs doing: a file whose latest outcome is a Failure and
    # that is still planned for a device folder that is currently mapped (a changed folder or a hidden LED resolves it).
    push_failed = any(latest.get(os.path.normcase(i.destination)) == "Failure" for i in items)
    if (_unresolved_download_failures(conn, event_id) or push_failed
            or any(m.status == mappings.STATUS_FAILED for m in enabled)):
        return STATUS_ATTENTION
    downloaded = conn.execute("SELECT 1 FROM download_history WHERE event_id = ? COLLATE NOCASE AND status = 'Success' "
                              "AND table_number IS NOT NULL LIMIT 1", (event_id,)).fetchone() is not None
    if downloaded and not items and not unmapped:
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
