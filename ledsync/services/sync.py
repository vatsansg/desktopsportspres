"""Synchronising downloaded files to the mapped LED devices (BRD Section 20, Steps 8.1 / 8.2).

For each successfully downloaded file, in order: determine table and LED type, retrieve the mapped destination, verify the
destination is reachable, copy the file, verify the transfer, record the sync status. Owner decisions (21/09/26):
  * A file the cloud removed (its local copy is already deleted) is removed from the device ONLY if this application put it
    there; any other file in the device folder is never touched.
  * A pushed file is verified by reading the copy back and comparing size and MD5 before it is renamed into place.
  * A file with the same name that this application did not put there is replaced.
No credentials are used: the Windows account the application runs under must be allowed to write to the shares.

What is pushed is decided from the database only (`plan`): a file is (re)pushed when it was downloaded and has never been
pushed to its current destination, its last push failed, or it was downloaded again after its last successful push. RPI files
have no LED destination and are never pushed. The record is `sync_history` (one row per outcome; the destination column holds
the full path of the file on the device).
"""

import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import connectivity, exceptions, localfiles, mappings, oplog, structure, transfer
from .progress import NullProgress

log = logging.getLogger(__name__)

OPERATION = "Synchronise"
PUSH, REMOVE = "Push", "Remove"
_ASCII_LOWER = {ord(c): ord(c) + 32 for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
MAX_ITEMS_PER_RUN = 5000


@dataclass(frozen=True)
class SyncItem:
    mapping: mappings.Mapping
    table: int
    led_type: str
    file_name: str
    action: str                    # PUSH | REMOVE
    source: Path | None            # the local copy (None for a removal)

    @property
    def destination(self) -> str:
        return os.path.join(self.mapping.shared_folder, self.file_name)


@dataclass
class SyncResult:
    pushed: int = 0
    removed: int = 0
    failed: int = 0
    remaining: int = 0
    cancelled: bool = False
    unreachable: list[str] = field(default_factory=list)      # LED destinations that could not be used
    failures: list[str] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)         # LEDs with downloaded files but no destination

    @property
    def total(self) -> int:
        return self.pushed + self.removed + self.failed + self.remaining


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def plan(conn: sqlite3.Connection, event_id: str, asset_event_dir) -> tuple[list[SyncItem], list[str]]:
    """(items to push or remove, labels of LEDs that have downloaded files but no destination). Database only."""
    mapped = {(m.table_number, m.led_type): m for m in mappings.list_mappings(conn, event_id)}
    latest: dict = {}
    for r in conn.execute("SELECT table_number, led_type, file_name, status, download_timestamp FROM download_history "
                          "WHERE event_id = ? COLLATE NOCASE AND table_number IS NOT NULL AND status IN ('Success', 'Deleted') "
                          "ORDER BY download_id", (event_id,)):
        led = structure.canonical_led_type(r["led_type"])
        if led is None or not r["file_name"]:
            continue
        latest[(r["table_number"], led, r["file_name"].translate(_ASCII_LOWER))] = (
            r["table_number"], led, r["file_name"], r["status"], r["download_timestamp"] or "")
    synced: dict = {}
    ours: set = set()                                           # destinations where WE put a file and have not since removed it
    for r in conn.execute("SELECT destination, status, sync_timestamp FROM sync_history WHERE event_id = ? COLLATE NOCASE "
                          "ORDER BY sync_id", (event_id,)):
        key = os.path.normcase(r["destination"] or "")
        synced[key] = (r["status"], r["sync_timestamp"] or "")
        if r["status"] == "Success":
            ours.add(key)
        elif r["status"] == "Deleted":
            ours.discard(key)
    shared: dict = {}

    items: list[SyncItem] = []
    unmapped: set[str] = set()
    for table, led, name, status, downloaded_at in latest.values():
        m = mapped.get((table, led))
        if m is None:
            continue                                            # the LED is not (or no longer) part of this event
        if not m.shared_folder:
            if status == "Success":
                unmapped.add(m.label)
            continue
        key = os.path.normcase(os.path.join(m.shared_folder, name))
        last = synced.get(key)
        if status == "Success":
            if last is None or last[0] != "Success" or last[1] < downloaded_at:
                source = (Path(asset_event_dir) / f"Table {table}" / structure.LED_LABELS[led] / name
                          if asset_event_dir is not None else None)
                items.append(SyncItem(m, table, led, name, PUSH, source))
        elif key in ours:                                       # removed in the cloud, and WE put it on the device
            if m.mapping_id not in shared:
                shared[m.mapping_id] = _folder_is_shared(conn, m)
            if not shared[m.mapping_id]:                        # another LED or event uses this folder: never remove from it
                items.append(SyncItem(m, table, led, name, REMOVE, None))
    order = {t: i for i, t in enumerate(structure.LED_TYPES)}
    items.sort(key=lambda i: (i.table, order.get(i.led_type, 9), i.file_name.casefold()))
    return items, sorted(unmapped)


def _folder_is_shared(conn: sqlite3.Connection, mapping: mappings.Mapping) -> bool:
    """True if any other mapping (of any event) points at the same folder: a file there may belong to that mapping."""
    mine = mappings.folder_key(mapping.shared_folder)
    for r in conn.execute("SELECT mapping_id, shared_folder FROM led_mappings WHERE enabled = 1 AND shared_folder != '' "
                          "AND mapping_id != ?", (mapping.mapping_id,)):
        if mappings.folder_key(r["shared_folder"]) == mine:
            return True
    return False


def latest_outcomes(conn: sqlite3.Connection, event_id: str) -> dict:
    """The latest recorded outcome for every device path (normalised): {path: 'Success' | 'Failure' | 'Deleted'}."""
    latest: dict = {}
    for r in conn.execute("SELECT destination, status FROM sync_history WHERE event_id = ? COLLATE NOCASE ORDER BY sync_id",
                          (event_id,)):
        latest[os.path.normcase(r["destination"] or "")] = r["status"]
    return latest


def _record(conn, event_id, item: SyncItem, status: str, message: str = "") -> None:
    conn.execute("INSERT INTO sync_history (event_id, file_name, destination, sync_timestamp, status, error_message) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (event_id, item.file_name, item.destination, _now(), status, message or None))


def _fail(conn, result: SyncResult, event_id, item: SyncItem, message: str, category: str, *, log_exception: bool = True) -> None:
    result.failed += 1
    result.failures.append(f"{item.mapping.label} {item.file_name}: {message}")
    try:
        _record(conn, event_id, item, "Failure", message)
        oplog.add(conn, OPERATION, "Failed", f"{item.mapping.label} {item.file_name}: {message}", event_id)
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
    if log_exception:
        exceptions.record(conn, category, OPERATION, message, event_id=event_id, table_number=item.table,
                          led_type=item.led_type, file_name=item.file_name, destination=item.mapping.shared_folder)


def _resolve(conn, event_id, item: SyncItem) -> None:
    """A successful push or removal resolves the earlier failure of that file to that device."""
    exceptions.resolve_matching(conn, event_id, OPERATION, table_number=item.table, led_type=item.led_type,
                                file_name=item.file_name, destination=item.mapping.shared_folder)


def _retry(action):
    """One more try after a short pause for a transient network problem or a copy that did not verify."""
    try:
        return action()
    except (localfiles.IntegrityError, localfiles.DeviceError) as err:
        if isinstance(err, localfiles.DeviceError) and err.category != exceptions.NETWORK_DEVICE:
            raise
        time.sleep(transfer.RETRY_DELAY)
        return action()


def process(conn: sqlite3.Connection, event_id: str, items: list[SyncItem], data_dir, progress=None, checker=None,
            protected=()) -> SyncResult:
    """Carry out `items`. Every destination is tested first; a destination that cannot be used fails only its own files.
    `protected` are folders no device folder may overlap (the local asset and RPI folders)."""
    progress = progress or NullProgress()
    result = SyncResult()
    if not items:
        return result
    involved = {i.mapping.mapping_id: i.mapping for i in items}
    outcomes = (checker or connectivity.check_many)({mid: m.shared_folder for mid, m in involved.items()},
                                                    forbidden_roots=(data_dir, *[p for p in protected if p]))
    usable: set[int] = set()
    for mid, m in involved.items():
        check = outcomes[mid]
        if check.ok:
            try:
                localfiles.check_destination(m.shared_folder, data_dir, protected)
                usable.add(mid)
            except localfiles.LocalFileError as err:
                if not isinstance(err, localfiles.DeviceError):
                    err = localfiles.DeviceError(exceptions.NETWORK_DEVICE, str(err))
                check = connectivity.CheckResult(False, str(err), err.category)
        try:
            mappings.record_test(conn, m, check.ok and mid in usable, check.message, check.category, check.warning)
        except mappings.MappingError:
            pass
        if mid not in usable:
            result.unreachable.append(m.label)

    dead: set[int] = set()                                      # destinations that failed after a retry during this run
    for index, item in enumerate(items):
        if progress.cancelled:
            result.cancelled, result.remaining = True, len(items) - index
            break
        if index >= MAX_ITEMS_PER_RUN:
            result.remaining = len(items) - index
            break
        mid = item.mapping.mapping_id
        progress.begin(item.file_name, None)
        ok = False
        if mid not in usable:
            _fail(conn, result, event_id, item, "The device folder could not be used, so the file was not sent.",
                  exceptions.NETWORK_DEVICE, log_exception=False)        # the device test already raised the exception row
        elif mid in dead:
            _fail(conn, result, event_id, item, "Not sent: an earlier file to this device failed.", exceptions.SYNCHRONISATION,
                  log_exception=False)
        else:
            try:
                folder = Path(item.mapping.shared_folder)
                if item.action == PUSH:
                    size = item.source.stat().st_size if item.source is not None and item.source.is_file() else 0
                    progress.begin(item.file_name, size)
                    target = _retry(lambda: localfiles.push_file(
                        item.source, folder, item.file_name, on_bytes=progress.add_bytes, cancelled=lambda: progress.cancelled))
                    _record(conn, event_id, item, "Success")
                    oplog.add(conn, OPERATION, "Success", f"{item.mapping.label}: sent {item.file_name}.", event_id)
                    _resolve(conn, event_id, item)
                else:
                    localfiles.delete_file(folder, item.file_name)
                    _record(conn, event_id, item, "Deleted")
                    oplog.add(conn, OPERATION, "Success", f"{item.mapping.label}: removed {item.file_name}.", event_id)
                    _resolve(conn, event_id, item)
                conn.commit()
                if item.action == PUSH:
                    result.pushed += 1
                    progress.tally_synchronised()
                else:
                    result.removed += 1
                ok = True
            except localfiles.Cancelled:
                conn.rollback()
                result.cancelled, result.remaining = True, len(items) - index
                progress.finish_file(False)
                break
            except localfiles.IntegrityError as err:
                conn.rollback()
                _fail(conn, result, event_id, item, str(err), exceptions.SYNCHRONISATION)
            except localfiles.DeviceError as err:
                conn.rollback()
                if err.category == exceptions.NETWORK_DEVICE:
                    dead.add(mid)
                _fail(conn, result, event_id, item, str(err), err.category)
            except localfiles.LocalFileError as err:
                conn.rollback()
                _fail(conn, result, event_id, item, str(err), exceptions.FILE_ACCESS)
            except sqlite3.Error:
                conn.rollback()
                log.exception("Could not record a synchronised file")
                result.failed += 1
                result.failures.append(f"{item.mapping.label} {item.file_name}: the result could not be saved.")
        if not ok:
            progress.tally_error()
        progress.finish_file(ok)
    if result.pushed or result.removed:
        try:
            conn.execute("UPDATE events SET last_sync = ? WHERE event_id = ? COLLATE NOCASE", (_now(), event_id))
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
    return result
