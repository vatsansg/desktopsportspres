"""The download / removal loop shared by RPI files and Table / LED files.

For each item the comparison marked DOWNLOAD (or repair) the real blob is located (the change log says
`Table 1/Inner/x.png` or `RPI/x.png`, Azure holds `Table 1/inner/x.png` / `rpi/x.png`), streamed to a temporary file in
the destination folder, checked against the size (and MD5, when Azure has one) that Azure lists, and only then moved into
place. For each item marked DELETE the local copy is removed. Every outcome is recorded in `download_history` (the local
change log), the operation log and, on failure, the exception log.

Behaviour that matters:
  * One bad file never stops the others - except that a connection or permission problem ends the run (every remaining
    file would fail the same slow way) after ONE automatic retry.
  * A wrong download (size or checksum) is discarded and retried once; the previous copy of the file is untouched.
  * The operator can cancel between chunks; nothing half-written is left under a real name.
  * Azure is only ever read.
"""

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import exceptions, localfiles, oplog
from .storage import StorageError

log = logging.getLogger(__name__)

MAX_FILES_PER_RUN = 2000
RETRY_DELAY = 2.0                                 # seconds before the single automatic retry
STOP_CATEGORIES = (exceptions.STORAGE_CONNECTIVITY, exceptions.PERMISSION)


@dataclass
class TransferResult:
    downloaded: int = 0
    removed: int = 0
    failed: int = 0
    gone: int = 0                    # downloaded before, missing from disk, and no longer in Azure: dropped from the list
    remaining: int = 0               # files not attempted (run limit, cancelled, or the run was stopped)
    stopped: bool = False
    cancelled: bool = False
    failures: list[str] = field(default_factory=list)      # "file name: plain reason", safe to show

    @property
    def total(self) -> int:
        return self.downloaded + self.removed + self.failed + self.gone + self.remaining

    def add(self, other: "TransferResult") -> None:
        self.downloaded += other.downloaded
        self.removed += other.removed
        self.failed += other.failed
        self.gone += other.gone
        self.remaining += other.remaining
        self.stopped = self.stopped or other.stopped
        self.cancelled = self.cancelled or other.cancelled
        self.failures += other.failures


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def history(conn, event_id, item, table, led_type, source: str, local: str, status: str) -> None:
    conn.execute("INSERT INTO download_history (event_id, file_name, table_number, led_type, source_path, local_path, "
                 "source_timestamp, download_timestamp, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (event_id, item.file_name, table, led_type, source, local, item.cloud_time_text, _now(), status))


def _retry(action):
    """Run `action`; a transient problem (connection, wrong checksum) gets ONE more try after a short pause."""
    try:
        return action()
    except (StorageError, localfiles.IntegrityError) as err:
        changed = isinstance(err, StorageError) and err.category == exceptions.DOWNLOAD and "changed in Azure" in err.message
        if isinstance(err, StorageError) and err.category not in STOP_CATEGORIES and not changed:
            raise
        time.sleep(RETRY_DELAY)
        return action()


def run(conn: sqlite3.Connection, storage, account: str, location, event_id: str, items, *, operation: str,
        folder_for, existing_folder_for, table_led_of, progress, listings: dict) -> TransferResult:
    """Carry out `items` (Assessments). `folder_for(item)` creates and returns the destination folder,
    `existing_folder_for(item)` returns it only if it exists (for removals), `table_led_of(item)` gives the
    (table number or None, LED type) recorded in the history."""
    result = TransferResult()
    for index, item in enumerate(items):
        if progress.cancelled:
            result.cancelled, result.remaining = True, len(items) - index
            break
        if index >= MAX_FILES_PER_RUN:
            result.remaining = len(items) - index
            break
        table, led = table_led_of(item)
        progress.begin(item.file_name, None)
        ok = False
        try:
            if item.action == "Download":
                tries = []

                def attempt():
                    if tries:
                        listings.clear()                                  # a retry must see Azure as it is NOW, not the cached list
                    tries.append(1)
                    info = storage.find_blob(location, item.path, listings)
                    if info.size is None:
                        raise StorageError(exceptions.DOWNLOAD, "Azure did not report the size of this file.")
                    progress.begin(item.file_name, info.size)                 # a retry starts the bar again
                    destination = folder_for(item)
                    written = localfiles.write_stream(
                        destination, item.file_name, storage.iter_blob(location, info.path, info.size, etag=info.etag),
                        size=info.size, md5=info.md5, on_bytes=progress.add_bytes, cancelled=lambda: progress.cancelled)
                    return info, written

                info, target = _retry(attempt)
                history(conn, event_id, item, table, led, location.blob_url(account, info.path), str(target), "Success")
                oplog.add(conn, operation, "Success", f"Downloaded {item.file_name}.", event_id)
                result.downloaded += 1
                progress.tally_downloaded()
            else:
                folder = existing_folder_for(item)
                if folder is not None:
                    localfiles.delete_file(folder, item.file_name)
                history(conn, event_id, item, table, led, location.blob_url(account, item.path),
                        str(folder / item.file_name) if folder is not None else "", "Deleted")
                oplog.add(conn, operation, "Success", f"Removed {item.file_name}.", event_id)
                result.removed += 1
            conn.commit()
            ok = True
        except localfiles.Cancelled:
            conn.rollback()
            result.cancelled, result.remaining = True, len(items) - index
            progress.finish_file(False)
            break
        except (StorageError, localfiles.LocalFileError) as err:
            conn.rollback()
            if item.repair and isinstance(err, StorageError) and err.category == exceptions.MISSING_FOLDER:
                # It was downloaded once, is gone from disk AND from Azure, and the log never removed it: record that
                # it no longer exists, so it stops being offered (a Failure row would come back every run).
                try:
                    history(conn, event_id, item, table, led, location.blob_url(account, item.path), "", "Deleted")
                    oplog.add(conn, operation, "Success", f"{item.file_name} is no longer in Azure and is no longer listed.", event_id)
                    conn.commit()
                    result.gone += 1
                    progress.finish_file(True)
                    continue
                except sqlite3.Error:
                    conn.rollback()
            _fail(conn, result, event_id, item, table, led, err, operation)
            progress.tally_error()
            if isinstance(err, StorageError) and err.category in STOP_CATEGORIES:
                result.stopped = True
                result.remaining = len(items) - index - 1
                progress.finish_file(False)
                break
        except sqlite3.Error:
            conn.rollback()
            log.exception("Could not record a downloaded file")
            result.failed += 1
            progress.tally_error()
            result.failures.append(f"{item.file_name}: the result could not be saved.")
        progress.finish_file(ok)
    if result.downloaded:
        try:
            conn.execute("UPDATE events SET last_download = ? WHERE event_id = ? COLLATE NOCASE", (_now(), event_id))
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
    return result


def _fail(conn, result: TransferResult, event_id, item, table, led, err, operation) -> None:
    if isinstance(err, StorageError):
        category, message = err.category, err.message
    else:
        category, message = exceptions.FILE_ACCESS, str(err)
    result.failed += 1
    result.failures.append(f"{item.file_name}: {message}")
    try:
        if item.action == "Download":
            history(conn, event_id, item, table, led, "", "", "Failure")
        oplog.add(conn, operation, "Failed", f"{item.file_name}: {message}", event_id)
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
    exceptions.record(conn, category, operation, message, event_id=event_id, file_name=item.file_name,
                      table_number=table, led_type=led)
