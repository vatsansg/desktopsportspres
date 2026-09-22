"""The local change log `_localchangelog.csv` (BRD Section 17, Step 6.2).

The record of what THIS application has downloaded lives in the database (`download_history`):
it is transactional, so a crash mid-run cannot corrupt it. `_localchangelog.csv` is the BRD's
human-readable copy of that record (owner decision 21/09/26), written from the database with the
BRD Section 17 columns, one file for all events, in the application data folder. It is rewritten
atomically and its failure is never fatal (the file may be open in Excel).

Cells that start with `= + - @` are prefixed with an apostrophe so a spreadsheet can never treat a
file name as a formula.
"""

import csv
import logging
import os
import sqlite3
import uuid
from pathlib import Path

from . import structure
from .events import format_timestamp

log = logging.getLogger(__name__)

FILENAME = "_localchangelog.csv"
HEADERS = ("Serial Number", "Event ID", "Table", "LED Type", "File Name", "Source Location", "Local Location",
           "Timestamp", "Source Updated Timestamp", "Download Status", "Sync Status")


# The OWASP set, plus a line feed and the full-width look-alikes some spreadsheets also accept.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r", "\n", "\uff1d", "\uff0b", "\u2212", "\uff20")


def path_for(data_dir) -> Path:
    return Path(data_dir) / FILENAME


def _safe(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in _FORMULA_STARTS else text


def _sync_lookup(conn: sqlite3.Connection):
    """(device folder per event/table/LED, latest sync status per event/destination) - to fill the Sync Status column."""
    folders = {(r["event_id"].casefold(), r["table_number"], r["led_type"]): r["shared_folder"]
               for r in conn.execute("SELECT event_id, table_number, led_type, shared_folder FROM led_mappings "
                                     "WHERE shared_folder <> ''")}
    latest = {}
    for r in conn.execute("SELECT event_id, destination, status FROM sync_history ORDER BY sync_id"):
        latest[(r["event_id"].casefold(), os.path.normcase(r["destination"] or ""))] = r["status"]
    return folders, latest


def _rows(conn: sqlite3.Connection, tz=None):
    folders, latest = _sync_lookup(conn)
    for r in conn.execute("SELECT * FROM download_history ORDER BY download_id"):
        canonical = structure.canonical_led_type(r["led_type"])
        led = structure.LED_LABELS.get(canonical or "", r["led_type"] or "")
        table = f"Table {r['table_number']}" if r["table_number"] is not None else ""
        folder = folders.get(((r["event_id"] or "").casefold(), r["table_number"], canonical)) if canonical else None
        sync_status = latest.get(((r["event_id"] or "").casefold(),
                                  os.path.normcase(os.path.join(folder, r["file_name"] or "")))) if folder else ""
        yield (r["download_id"], r["event_id"], table, led, r["file_name"], r["source_path"], r["local_path"],
               format_timestamp(r["download_timestamp"], tz) if r["download_timestamp"] else "",
               r["source_timestamp"] or "", r["status"] or "", sync_status or "")


def write(data_dir, conn: sqlite3.Connection | None = None, tz=None) -> bool:
    """(Re)write the file from the database - headers only when there is no database or no history.
    Atomic (unique temporary file, then replace). Returns False, never raises, if it cannot be written.
    If the database cannot be READ, an existing file is left exactly as it is (it is never replaced by
    an empty one)."""
    target = path_for(data_dir)
    temp = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        rows = list(_rows(conn, tz)) if conn is not None else []
    except sqlite3.Error:
        log.warning("The local change log was not rewritten because the database could not be read.")
        if target.exists():
            return False
        rows = []
    try:
        with open(temp, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(HEADERS)
            for row in rows:
                writer.writerow([_safe(cell) for cell in row])
        os.replace(temp, target)
        return True
    except (OSError, UnicodeError):
        log.warning("The local change log could not be written (it may be open in another program).")
        try:
            os.remove(temp)
        except OSError:
            pass
        return False


def refresh(config) -> bool:
    """At start-up: make sure the file exists and matches the database."""
    from ..db import connect
    if not config.db_path.exists():                     # never create a database as a side effect
        return False if path_for(config.data_dir).exists() else write(config.data_dir, None)
    conn = None
    try:
        conn = connect(config.db_path)
        return write(config.data_dir, conn)
    except (sqlite3.Error, OSError):
        return write(config.data_dir, None) if not path_for(config.data_dir).exists() else False
    finally:
        if conn is not None:
            conn.close()
