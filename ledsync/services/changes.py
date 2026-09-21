"""Comparing the cloud change log with what has already been processed (BRD Section 18, Step 6.3).

Rules (owner decisions 21/09/26 and the web BRD v2.4 note):
  * A file is identified by its FULL path - table + LED type + file name. Letter case is ignored for
    A-Z only; every other character must match exactly. (Python's `casefold` also equates `ss`
    with `ß` and the Kelvin sign with `k`, which Windows keeps apart; merging them could hide a
    file. If two entries differ only that way, or only by accents/Unicode form, BOTH are listed as
    NOT APPLICABLE with a reason - they would be the same file on disk.)
  * Table numbers are plain digits 0-9 and are normalised (`Table 01` is Table 1), so the same file
    written two ways is one entry.
  * Only the LATEST cloud entry for a path matters (by change time; on an exact tie the later line
    in the file). It is compared with the latest local record (a successful download or a
    deletion) for the same path, comparing UTC instants parsed from the change log's own
    timestamps - never this computer's clock.
  * New / Updated in the cloud, and nothing (or something older) here -> DOWNLOAD.
  * Deleted in the cloud, and a successful local copy older than that -> DELETE LOCAL COPY
    (owner decision; the deletion itself is carried out in Phase 7 - nothing here touches a file).
  * Everything else that is up to date -> ALREADY PROCESSED.
  * `RPI/<file>` (directly inside the event's `RPI` folder) is its own destination: those files go
    to the RPI folder set in Settings, not to a table or LED device (owner decision 21/09/26).
  * A file with no extension is not an asset and is NOT APPLICABLE (owner decision 21/09/26).
  * Entries that are not `Table N/<LED type>/<file>` for a table and LED type the event actually
    enables (or `RPI/<file>`) are listed as NOT APPLICABLE, with the reason, and are never acted on.
  * Files that ARE in Azure but have no change-log entry at all (the web application does not always log files it
    copies) are found by also listing each enabled Table / LED folder in Azure, and are offered as
    "New (not in log)" when they are not stored locally yet (owner decision 21/09/26). The change log always wins
    for any path it mentions.
  * The "Last Updated Timestamp Cut-off" (BRD Section 14) is still an open BRD item; the owner
    chose UTC comparison with no cut-off for now (a setting comes with Phase 10).

Comparing only reads and decides: it changes no file and no database row.
"""

import logging
import os
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from . import cloud, exceptions, oplog
from . import registration as reg
from . import structure
from .changelog import (
    ChangeLogError, CloudEntry, ParsedChangeLog, SkippedRow, _path_problem, blocked_name_problem, fetch_change_log,
)
from .events import parse_timestamp
from .storage import StorageError

log = logging.getLogger(__name__)

DOWNLOAD = "Download"
DELETE = "Delete local copy"
DONE = "Already processed"
NOT_APPLICABLE = "Not applicable"

LABEL_NEW, LABEL_UPDATED, LABEL_REMOVED = "New", "Updated", "Removed in cloud"
LABEL_NEW_UNLOGGED = "New (not in log)"
LED_FOLDER_NAMES = {"Inner": ("Inner",), "Outer": ("Outer",), "MainLED": ("MainLED", "Main LED")}
NOT_ASSETS = frozenset({"keepalive.txt"})          # placeholders the web application uses to create folders
RPI = "RPI"                        # the pseudo LED type of files in the event's RPI folder

_TABLE_RE = re.compile(r"^Table ([1-9][0-9]{0,3})$", re.IGNORECASE)     # ASCII digits only, no leading zero
_TABLE_ZERO_RE = re.compile(r"^Table (0[0-9]{0,3})$", re.IGNORECASE)
LED_ORDER = {t: i for i, t in enumerate(structure.LED_TYPES)}
_ASCII_LOWER = {ord(c): ord(c) + 32 for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
FUTURE_TOLERANCE = timedelta(days=1)


def fold(text: str) -> str:
    """Ignore case for A-Z only (see the module docstring)."""
    return text.translate(_ASCII_LOWER)


def path_key(table, led_type: str, file_name: str) -> str:
    if led_type == RPI:
        return f"rpi/{fold(file_name)}"
    return f"{int(table)}/{led_type.casefold()}/{fold(file_name)}"


def has_extension(file_name: str) -> bool:
    """`a.png` yes; `HOME_Look` and `.hidden` no."""
    return os.path.splitext(file_name)[1] not in ("", ".")


def _loose(key: str) -> str:
    """The most generous notion of 'the same name': used only to DETECT names that would collide on disk."""
    return unicodedata.normalize("NFC", key).casefold()


@dataclass(frozen=True)
class LocalRecord:
    status: str                       # Success | Deleted
    source_time: datetime


@dataclass(frozen=True)
class Assessment:
    path: str                         # as written in the cloud change log
    table: int | None
    led_type: str | None              # canonical: Inner | Outer | MainLED
    file_name: str
    cloud_status: str                 # New | Updated | Deleted
    cloud_time_text: str
    cloud_time: datetime
    action: str                       # DOWNLOAD | DELETE | DONE | NOT_APPLICABLE
    label: str                        # New | Updated | Removed in cloud | Already processed | Not applicable
    reason: str
    revisions: int                    # how many log entries there are for this path
    local_status: str = ""            # the latest local record for this path: Success | Deleted | "" (none)
    in_log: bool = True               # False: the file is in Azure but the change log does not mention it


@dataclass(frozen=True)
class Comparison:
    assessments: tuple[Assessment, ...]
    skipped: tuple[SkippedRow, ...]
    total_entries: int
    source_name: str
    azure_note: str = ""              # why Azure's own file list could not be compared (empty = it was)

    def with_action(self, action: str) -> list[Assessment]:
        return [a for a in self.assessments if a.action == action]

    def count(self, action: str) -> int:
        return sum(1 for a in self.assessments if a.action == action)

    def count_label(self, label: str) -> int:
        return sum(1 for a in self.assessments if a.label == label)

    @property
    def distinct_paths(self) -> int:
        return len(self.assessments)


def local_state(conn: sqlite3.Connection, event_id: str) -> dict[str, LocalRecord]:
    """The latest successful download or deletion per path (failed downloads do not count).
    Rows that cannot be understood are ignored, never fatal."""
    latest: dict[str, LocalRecord] = {}
    rows = conn.execute("SELECT table_number, led_type, file_name, source_timestamp, status FROM download_history "
                        "WHERE event_id = ? COLLATE NOCASE AND status IN ('Success', 'Deleted') "
                        "ORDER BY download_id", (event_id,)).fetchall()
    for r in rows:
        when = parse_timestamp(r["source_timestamp"])
        if (r["led_type"] or "").strip().casefold() == RPI.casefold():
            led, table = RPI, None
        else:
            led = structure.canonical_led_type(r["led_type"])
            try:
                table = int(r["table_number"])
            except (TypeError, ValueError):
                continue
        if led is None or not r["file_name"] or when is None:
            continue
        key = path_key(table, led, r["file_name"])
        current = latest.get(key)
        if current is None or when >= current.source_time:
            latest[key] = LocalRecord(r["status"], when)
    return latest


def _classify_path(path: str, enabled: set) -> tuple[int | None, str, str] | str:
    """(table or None, LED type or "RPI", file name) for a usable path, or the reason it is not applicable."""
    where = _classify_folder(path, enabled)
    if isinstance(where, tuple) and not has_extension(where[2]):
        return "The file has no extension, so it is not an asset."
    return where


def _classify_folder(path: str, enabled: set) -> tuple[int | None, str, str] | str:
    parts = path.split("/")
    if fold(parts[0]) == "rpi":
        if len(parts) != 2:
            return "Files inside sub-folders of RPI are not supported."
        return None, RPI, parts[1]
    if len(parts) < 3:
        return "The file is not inside a Table / LED-type folder."
    if len(parts) > 3:
        return "Files inside sub-folders are not supported."
    match = _TABLE_RE.match(parts[0])
    if not match:
        if _TABLE_ZERO_RE.match(parts[0]):
            return "The table number is not valid (Table 0 or a number with a leading zero)."
        return "The file is not inside a Table folder."
    led = structure.canonical_led_type(parts[1])
    if led is None:
        return "The folder is not a known LED type (Inner, Outer or Main LED)."
    table = int(match.group(1))
    if (table, led) not in enabled:
        return f"Table {table} {structure.LED_LABELS[led]} is not part of this event."
    return table, led, parts[2]


def compare(parsed: ParsedChangeLog, event_structure: structure.EventStructure,
            local: dict[str, LocalRecord]) -> Comparison:
    enabled = set(event_structure.enabled_pairs)
    groups: dict[str, list[tuple[CloudEntry, tuple | str]]] = {}
    for entry in parsed.entries:
        where = _classify_path(entry.path, enabled)
        key = path_key(*where) if isinstance(where, tuple) else "?" + fold(entry.path)
        groups.setdefault(key, []).append((entry, where))

    # Two different entries that would be ONE file on disk (accents, Unicode form, ss/ß ...) are not
    # merged and not queued: both are listed as not applicable so nothing is silently hidden or overwritten.
    by_loose: dict[str, list[str]] = {}
    for key in groups:
        if not key.startswith("?"):
            by_loose.setdefault(_loose(key), []).append(key)
    colliding = {key for keys in by_loose.values() if len(keys) > 1 for key in keys}

    out: list[Assessment] = []
    for key, members in groups.items():
        latest, where = max(members, key=lambda m: (m[0].timestamp, m[0].line))
        count = len(members)
        if key in colliding:
            out.append(Assessment(latest.path, None, None, latest.path.rsplit("/", 1)[-1], latest.status,
                                  latest.timestamp_text, latest.timestamp, NOT_APPLICABLE, NOT_APPLICABLE,
                                  "Another file has a name that differs only in accents or letter form, so they "
                                  "would be the same file on this computer. Ask the web-app team to rename one.",
                                  count))
        elif isinstance(where, str):
            out.append(Assessment(latest.path, None, None, latest.path.rsplit("/", 1)[-1], latest.status,
                                  latest.timestamp_text, latest.timestamp, NOT_APPLICABLE, NOT_APPLICABLE, where, count))
        else:
            table, led, name = where
            have = local.get(key)
            action, label, reason = _decide(latest, have)
            out.append(Assessment(latest.path, table, led, name, latest.status, latest.timestamp_text, latest.timestamp,
                                  action, label, reason, count, have.status if have else ""))
    out.sort(key=_sort_key)
    return Comparison(tuple(out), parsed.skipped, len(parsed.entries), parsed.source_name)


def _sort_key(a: Assessment):
    return (a.action == NOT_APPLICABLE, a.table if a.table is not None else 10**6,
            LED_ORDER.get(a.led_type or "", 99), a.path.casefold(), a.path)


def add_azure_files(comparison: Comparison, storage, location, event_structure: structure.EventStructure,
                    local: dict[str, LocalRecord]) -> Comparison:
    """Also compare Azure's own file list: files in an enabled Table / LED folder that the change log never mentions
    are offered for download (unless already stored). Read-only listing; a problem is reported, never fatal."""
    logged = {path_key(a.table, a.led_type, a.file_name) for a in comparison.assessments if a.table is not None}
    logged_loose = {_loose(k) for k in logged}
    logged_paths = {fold(a.path) for a in comparison.assessments}
    extras: list[Assessment] = []
    seen: set[str] = set()
    notes: list[str] = []
    listings: dict = {}
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    for table, led in event_structure.enabled_pairs:
        found: dict = {}
        try:
            for folder in LED_FOLDER_NAMES[led]:
                for info in storage.list_files(location, f"Table {table}/{folder}", listings):
                    found[info.path.rsplit("/", 1)[-1]] = info
        except StorageError as err:
            notes.append(err.message)
            continue
        for name, info in found.items():
            if (_path_problem(name) or blocked_name_problem(name) or not has_extension(name)
                    or name.casefold() in NOT_ASSETS):
                continue
            key = path_key(table, led, name)
            if key in logged or key in seen or fold(f"Table {table}/{led}/{name}") in logged_paths:
                continue
            if _loose(key) in logged_loose or any(_loose(key) == _loose(s) for s in seen):
                continue                                              # would be the same file on disk as another entry
            seen.add(key)
            have = local.get(key)
            when = parse_timestamp(info.modified) or epoch
            action, label, reason = ((DOWNLOAD, LABEL_NEW_UNLOGGED, "In Azure but not in the change log.") if have is None
                                     else (DONE, DONE, "Already downloaded."))
            extras.append(Assessment(f"Table {table}/{led}/{name}", table, led, name, "New", info.modified,
                                     when, action, label, reason, 0, have.status if have else "", False))
    note = " ".join(dict.fromkeys(notes))
    if not extras and not note:
        return comparison
    merged = sorted([*comparison.assessments, *extras], key=_sort_key)
    return replace(comparison, assessments=tuple(merged), azure_note=note)


def _decide(latest: CloudEntry, have: LocalRecord | None) -> tuple[str, str, str]:
    if latest.status == "Deleted":
        if have is not None and have.status == "Success" and have.source_time < latest.timestamp:
            return DELETE, LABEL_REMOVED, "Removed in the cloud after it was downloaded; the local copy will be deleted."
        if have is None:
            return DONE, DONE, "Removed in the cloud; nothing is stored locally."
        if have.status == "Success":
            return DONE, DONE, "The local copy is newer than the removal, so it is kept."
        return DONE, DONE, "The removal has already been processed."
    # New or Updated
    if have is None:
        return DOWNLOAD, LABEL_NEW, "Not downloaded yet."
    if have.source_time < latest.timestamp:
        if have.status == "Deleted":
            return DOWNLOAD, LABEL_NEW, "Added again in the cloud after it was removed."
        return DOWNLOAD, LABEL_UPDATED, "Changed in the cloud after it was last downloaded."
    return DONE, DONE, "Already downloaded or removed at or after this change."


# --- the whole check ------------------------------------------------------------------------------------

class CheckError(Exception):
    """A check that could not be completed. `message` is plain text that is safe to show."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category
        self.message = message


@dataclass(frozen=True)
class Report:
    event_id: str
    comparison: Comparison
    checked_at: datetime
    folder: str
    container: str = ""               # with `folder`, the verified event location in Azure
    guid: str = ""                    # the registration this result belongs to
    history_marker: tuple = ()        # (rows, newest id) of the local history when it was made
    future_dated: int = 0             # entries dated more than a day ahead of this computer's clock


def history_marker(conn: sqlite3.Connection, event_id: str) -> tuple:
    row = conn.execute("SELECT COUNT(*) AS n, COALESCE(MAX(download_id), 0) AS newest FROM download_history "
                       "WHERE event_id = ? COLLATE NOCASE", (event_id,)).fetchone()
    return (row["n"], row["newest"])


def is_current(conn: sqlite3.Connection, report: Report) -> bool:
    """A saved result is only valid for the registration and local history it was made from."""
    try:
        row = conn.execute("SELECT event_guid FROM events WHERE event_id = ? COLLATE NOCASE",
                           (report.event_id,)).fetchone()
        return (row is not None and reg.normalise_guid(row["event_guid"] or "") == report.guid
                and history_marker(conn, report.event_id) == report.history_marker)
    except sqlite3.Error:
        return False


def check_event(conn: sqlite3.Connection, storage, settings, event_id: str) -> Report:
    """Fetch the event's change log and compare it. Read-only against Azure; writes only an audit row
    (and, on failure, an exception row). Raises CheckError with a safe message."""
    operation = "Check Changes"
    try:
        row = conn.execute("SELECT event_id, event_guid FROM events WHERE event_id = ? COLLATE NOCASE",
                           (event_id,)).fetchone()
        if row is None:
            raise CheckError(exceptions.CONFIGURATION, "That event is not registered.")
        event_id = row["event_id"]
        try:
            event_structure = structure.load_structure(conn, event_id)
        except structure.StructureError as err:
            raise CheckError(exceptions.INVALID_CONFIGURATION, str(err)) from None
        registered = reg.normalise_guid(row["event_guid"] or "")
        if not registered:
            raise CheckError(exceptions.GUID_VALIDATION,
                             "This event has no recorded GUID. Re-register it before checking for changes.")

        fetched = cloud.fetch_event_config(storage, settings, event_id)
        if reg.normalise_guid(fetched.config.guid) != registered:
            raise CheckError(exceptions.GUID_VALIDATION,
                             "The web application has exported this event again (its GUID changed). "
                             "Re-register the event, then check for changes.")
        parsed = fetch_change_log(storage, fetched.location)
        marker = history_marker(conn, event_id)
        local = local_state(conn, event_id)
        comparison = compare(parsed, event_structure, local)
        comparison = add_azure_files(comparison, storage, fetched.location, event_structure, local)
    except CheckError as err:
        _log_failure(conn, event_id, operation, err.category, err.message)
        raise
    except (StorageError, ChangeLogError, reg.RegistrationError) as err:
        _log_failure(conn, event_id, operation, err.category, err.message)
        raise CheckError(err.category, err.message) from None
    except Exception:                                          # noqa: BLE001 - a check must never end in an error page
        log.exception("Unexpected problem while checking the change log")
        message = "The change log could not be checked because of an unexpected problem."
        _log_failure(conn, event_id, operation, exceptions.CONFIGURATION, message)
        raise CheckError(exceptions.CONFIGURATION, message) from None

    oplog.record(conn, operation, "Success",
                 f"{comparison.count(DOWNLOAD)} to download, {comparison.count(DELETE)} to remove, "
                 f"{comparison.count(DONE)} already processed, {comparison.count(NOT_APPLICABLE)} not applicable, "
                 f"{len(comparison.skipped)} unreadable row(s) in {comparison.source_name}; "
                 f"{comparison.count_label(LABEL_NEW_UNLOGGED)} file(s) found in Azure but not in the change log.", event_id)
    now = datetime.now(timezone.utc)
    future = sum(1 for e in parsed.entries if e.timestamp > now + FUTURE_TOLERANCE)
    return Report(event_id, comparison, now, fetched.location.folder, fetched.location.container, registered, marker,
                  future)


def _log_failure(conn, event_id, operation, category, message) -> None:
    try:
        exceptions.record(conn, category, operation, message, event_id=event_id)
        oplog.record(conn, operation, "Failed", message, event_id)
    except Exception:                                          # noqa: BLE001 - logging must never hide the real error
        log.exception("Could not record a failed change check")
