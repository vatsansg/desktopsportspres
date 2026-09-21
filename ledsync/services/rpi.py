"""Downloading the event's RPI files to the local RPI folder (owner decision 21/09/26).

Files the change log lists as `RPI/<file>` are not for a table or LED device: they are saved in
the RPI folder chosen in Settings (default: an `RPI` folder inside the application data folder).
For each file the comparison marked DOWNLOAD the real blob is located (the log says `RPI/x.png`,
Azure holds `rpi/x.png`), read with a size cap, and written safely (`localfiles`); for each file
marked DELETE the local copy is removed. A file the history says was downloaded but that is no longer
in the RPI folder (deleted by hand, or the folder was changed in Settings) is downloaded again.

Every outcome is recorded in `download_history` (the local change log), the operation log and, on
failure, the exception log. One bad file never stops the others - except that a connection or
permission problem ends the run (every remaining file would fail the same slow way), and one run
handles at most MAX_FILES_PER_RUN files. The RPI folder is shared by all events, so a file that another
event has already saved under the same name is never overwritten or removed by this event.
Azure is only ever read.
"""

import dataclasses
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import changes, exceptions, localfiles, oplog
from .storage import EventLocation, StorageError

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 50_000_000          # whole file is held in memory; streaming comes with Phase 7 (large videos)
MAX_FILES_PER_RUN = 200
OPERATION = "RPI Files"
STOP_CATEGORIES = (exceptions.STORAGE_CONNECTIVITY, exceptions.PERMISSION)


@dataclass
class RpiResult:
    downloaded: int = 0
    removed: int = 0
    kept: int = 0                    # removals not carried out because another event's file has that name
    failed: int = 0
    remaining: int = 0               # files not attempted (run limit, or the run was stopped)
    stopped: bool = False
    failures: list[str] = field(default_factory=list)      # "file name: plain reason", safe to show

    @property
    def total(self) -> int:
        return self.downloaded + self.removed + self.kept + self.failed + self.remaining


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rpi_items(comparison: changes.Comparison, root=None) -> list[changes.Assessment]:
    """What an RPI run would do. With `root`, files the history says were downloaded but that are missing
    from that folder are included (as downloads)."""
    items = [a for a in comparison.assessments
             if a.led_type == changes.RPI and a.action in (changes.DOWNLOAD, changes.DELETE)]
    if root is not None:
        candidates = [a for a in comparison.assessments
                      if a.led_type == changes.RPI and a.action == changes.DONE and a.local_status == "Success"
                      and a.cloud_status in ("New", "Updated")]
        present = localfiles.existing_names(root, [a.file_name for a in candidates]) if candidates else set()
        if present is not None:
            items += [dataclasses.replace(a, action=changes.DOWNLOAD, label=changes.LABEL_NEW,
                                          reason="The file is not in the RPI folder.")
                      for a in candidates if a.file_name not in present]
    return items


def _history(conn, event_id, item: changes.Assessment, source: str, local: str, status: str) -> None:
    conn.execute("INSERT INTO download_history (event_id, file_name, table_number, led_type, source_path, local_path, "
                 "source_timestamp, download_timestamp, status) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)",
                 (event_id, item.file_name, changes.RPI, source, local, item.cloud_time_text, _now(), status))


def _owned_by_another_event(conn, event_id: str, item: changes.Assessment, target: Path) -> str | None:
    """The other event whose latest record for this file name in this folder is a successful download."""
    rows = conn.execute("SELECT event_id, file_name, local_path, status FROM download_history "
                        "WHERE led_type = ? AND event_id <> ? COLLATE NOCASE ORDER BY download_id DESC",
                        (changes.RPI, event_id)).fetchall()
    seen = set()
    for r in rows:
        key = (r["event_id"].casefold(), changes.fold(r["file_name"]))
        if changes.fold(r["file_name"]) != changes.fold(item.file_name) or key in seen:
            continue
        seen.add(key)
        if r["status"] == "Success" and r["local_path"] and os.path.normcase(r["local_path"]) == os.path.normcase(str(target)):
            return r["event_id"]
    return None


def process(conn: sqlite3.Connection, storage, account: str, location: EventLocation, event_id: str,
            comparison: changes.Comparison, rpi_root, data_dir) -> RpiResult:
    """Carry out the RPI downloads and removals of `comparison`. Raises `localfiles.LocalFileError`
    only if the RPI folder itself cannot be used; problems with single files are counted."""
    result = RpiResult()
    listings: dict = {}                                       # one directory listing per folder, for the whole run
    if not any(a.led_type == changes.RPI and (a.action in (changes.DOWNLOAD, changes.DELETE) or a.local_status == "Success")
               for a in comparison.assessments):
        return result                                         # nothing to do: the folder is not even created
    root = localfiles.open_root(rpi_root, data_dir)
    pending = rpi_items(comparison, root)
    if not pending:
        return result

    for index, item in enumerate(pending):
        if index >= MAX_FILES_PER_RUN:
            result.remaining = len(pending) - index
            break
        try:
            target = root / item.file_name
            owner = _owned_by_another_event(conn, event_id, item, target)
            if item.action == changes.DOWNLOAD:
                if owner:
                    raise localfiles.LocalFileError(f"Event {owner} already saved a file with this name in the RPI folder, "
                                                    "so it was not replaced. Use a different RPI folder or remove that file.")
                real = storage.resolve_relative_path(location, item.path, listings)
                data = storage.read_blob(location, real, MAX_FILE_BYTES)
                if len(data) > MAX_FILE_BYTES:
                    raise StorageError(exceptions.DOWNLOAD, "The file is larger than this application can save "
                                                            f"({MAX_FILE_BYTES // 1_000_000} MB).")
                target = localfiles.write_atomic(root, item.file_name, data)
                _history(conn, event_id, item, location.blob_url(account, real), str(target), "Success")
                oplog.add(conn, OPERATION, "Success", f"Downloaded {item.file_name} to the RPI folder.", event_id)
                result.downloaded += 1
            elif owner:                                       # another event relies on this file: leave it, note the removal
                _history(conn, event_id, item, location.blob_url(account, item.path), "", "Deleted")
                oplog.add(conn, OPERATION, "Success", f"{item.file_name} was kept in the RPI folder (event {owner} uses it).", event_id)
                result.kept += 1
            else:
                localfiles.delete_file(root, item.file_name)
                _history(conn, event_id, item, location.blob_url(account, item.path), str(target), "Deleted")
                oplog.add(conn, OPERATION, "Success", f"Removed {item.file_name} from the RPI folder.", event_id)
                result.removed += 1
            conn.commit()
        except (StorageError, localfiles.LocalFileError) as err:
            conn.rollback()
            _fail(conn, result, event_id, item, err)
            if isinstance(err, StorageError) and err.category in STOP_CATEGORIES:
                result.stopped = True
                result.remaining = len(pending) - index - 1
                break
        except sqlite3.Error:
            conn.rollback()
            log.exception("Could not record an RPI file")
            result.failed += 1
            result.failures.append(f"{item.file_name}: the result could not be saved.")
    if result.downloaded:
        try:
            conn.execute("UPDATE events SET last_download = ? WHERE event_id = ? COLLATE NOCASE", (_now(), event_id))
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
    return result


def _fail(conn, result: RpiResult, event_id, item: changes.Assessment, err) -> None:
    if isinstance(err, StorageError):
        category, message = err.category, err.message
    else:
        category, message = exceptions.FILE_ACCESS, str(err)
    result.failed += 1
    result.failures.append(f"{item.file_name}: {message}")
    try:
        if item.action == changes.DOWNLOAD:
            _history(conn, event_id, item, "", "", "Failure")
        oplog.add(conn, OPERATION, "Failed", f"{item.file_name}: {message}", event_id)
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
    exceptions.record(conn, category, OPERATION, message, event_id=event_id, file_name=item.file_name)
