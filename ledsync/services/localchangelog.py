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
from pathlib import Path

from . import structure
from .events import format_timestamp

log = logging.getLogger(__name__)

FILENAME = "_localchangelog.csv"
HEADERS = ("Serial Number", "Event ID", "Table", "LED Type", "File Name", "Source Location", "Local Location",
           "Timestamp", "Source Updated Timestamp", "Download Status", "Sync Status")


def path_for(data_dir) -> Path:
    return Path(data_dir) / FILENAME


def _safe(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _rows(conn: sqlite3.Connection, tz=None):
    for r in conn.execute("SELECT * FROM download_history ORDER BY download_id"):
        led = structure.LED_LABELS.get(structure.canonical_led_type(r["led_type"]) or "", r["led_type"] or "")
        table = f"Table {r['table_number']}" if r["table_number"] is not None else ""
        yield (r["download_id"], r["event_id"], table, led, r["file_name"], r["source_path"], r["local_path"],
               format_timestamp(r["download_timestamp"], tz) if r["download_timestamp"] else "",
               r["source_timestamp"] or "", r["status"] or "",
               "")           # Sync Status: filled from the synchronisation history when Phase 8 exists


def write(data_dir, conn: sqlite3.Connection | None = None, tz=None) -> bool:
    """(Re)write the file from the database - headers only when there is no database or no history.
    Atomic (temporary file, then replace). Returns False, never raises, if it cannot be written."""
    target = path_for(data_dir)
    temp = target.with_name(target.name + ".tmp")
    try:
        rows = list(_rows(conn, tz)) if conn is not None else []
    except sqlite3.Error:
        rows = []
    try:
        with open(temp, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(HEADERS)
            for row in rows:
                writer.writerow([_safe(cell) for cell in row])
        os.replace(temp, target)
        return True
    except OSError:
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
