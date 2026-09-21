"""Downloading the event's RPI files to the local RPI folder (owner decision 21/09/26).

Files the change log lists as `RPI/<file>` are not for a table or LED device: they are saved in
the RPI folder chosen in Settings (default: an `RPI` folder inside the application data folder).
For each file the comparison marked DOWNLOAD the real blob is located (the log says `RPI/x.png`,
Azure holds `rpi/x.png`), read with a size cap, and written safely (`localfiles`); for each file
marked DELETE the local copy is removed. Every outcome is recorded in `download_history` (the
local change log), the operation log and, on failure, the exception log. One bad file never stops
the others. Azure is only ever read.
"""

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import changes, exceptions, localfiles, oplog
from .storage import EventLocation, StorageError

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 50_000_000          # whole file is held in memory; streaming comes with Phase 7 (large videos)
OPERATION = "RPI Files"


@dataclass
class RpiResult:
    downloaded: int = 0
    removed: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)      # "file name: plain reason", safe to show

    @property
    def total(self) -> int:
        return self.downloaded + self.removed + self.failed


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rpi_items(comparison: changes.Comparison) -> list[changes.Assessment]:
    return [a for a in comparison.assessments
            if a.led_type == changes.RPI and a.action in (changes.DOWNLOAD, changes.DELETE)]


def _history(conn, event_id, item: changes.Assessment, source: str, local: str, status: str) -> None:
    conn.execute("INSERT INTO download_history (event_id, file_name, table_number, led_type, source_path, local_path, "
                 "source_timestamp, download_timestamp, status) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)",
                 (event_id, item.file_name, changes.RPI, source, local, item.cloud_time_text, _now(), status))


def process(conn: sqlite3.Connection, storage, account: str, location: EventLocation, event_id: str,
            comparison: changes.Comparison, rpi_root, data_dir) -> RpiResult:
    """Carry out the RPI downloads and removals of `comparison`. Raises `localfiles.LocalFileError`
    only if the RPI folder itself cannot be used; problems with single files are counted."""
    result = RpiResult()
    items = rpi_items(comparison)
    if not items:
        return result
    root = localfiles.open_root(rpi_root, data_dir)

    for item in items:
        try:
            if item.action == changes.DOWNLOAD:
                real = storage.resolve_relative_path(location, item.path)
                data = storage.read_blob(location, real, MAX_FILE_BYTES)
                if len(data) > MAX_FILE_BYTES:
                    raise StorageError(exceptions.DOWNLOAD, "The file is larger than this application can save "
                                                            f"({MAX_FILE_BYTES // 1_000_000} MB).")
                target = localfiles.write_atomic(root, item.file_name, data)
                _history(conn, event_id, item, location.blob_url(account, real), str(target), "Success")
                oplog.add(conn, OPERATION, "Success", f"Downloaded {item.file_name} to the RPI folder.", event_id)
                result.downloaded += 1
            else:
                localfiles.delete_file(root, item.file_name)
                _history(conn, event_id, item, location.blob_url(account, item.path), str(root / item.file_name), "Deleted")
                oplog.add(conn, OPERATION, "Success", f"Removed {item.file_name} from the RPI folder.", event_id)
                result.removed += 1
            conn.commit()
        except (StorageError, localfiles.LocalFileError) as err:
            conn.rollback()
            _fail(conn, result, event_id, item, err)
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
