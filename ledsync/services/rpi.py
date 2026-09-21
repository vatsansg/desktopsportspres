"""The event's RPI files (owner decisions 21/09/26).

Files the change log lists as `RPI/<file>` are not for a table or LED device: they are saved in a sub-folder per event of the
RPI folder chosen in Settings - `<RPI folder>\\<Event ID>\\<file>` (default RPI folder: `RPI` inside the application data
folder). A file the history says was downloaded but that is no longer in that folder (deleted by hand, or the folder was
changed in Settings) is downloaded again. The download itself is the shared loop in `transfer.py`.
"""

import dataclasses
from pathlib import Path

from . import changes, localfiles, transfer
from .progress import NullProgress
from .storage import EventLocation

OPERATION = "RPI Files"
RpiResult = transfer.TransferResult


def event_folder(rpi_root, event_id: str) -> Path:
    """Where this event's RPI files go (no disk access)."""
    return localfiles.subfolder_path(rpi_root, event_id)


def rpi_items(comparison: changes.Comparison, event_dir=None) -> list[changes.Assessment]:
    """What an RPI run would do. With `event_dir`, files the history says were downloaded but that are missing from that
    folder are included (as downloads)."""
    items = [a for a in comparison.assessments
             if a.led_type == changes.RPI and a.action in (changes.DOWNLOAD, changes.DELETE)]
    if event_dir is not None:
        candidates = [a for a in comparison.assessments
                      if a.led_type == changes.RPI and a.action == changes.DONE and a.local_status == "Success"
                      and a.cloud_status in ("New", "Updated")]
        missing = localfiles.missing_files(Path(event_dir), [a.file_name for a in candidates]) if candidates else set()
        if missing is not None:
            items += [dataclasses.replace(a, action=changes.DOWNLOAD, label=changes.LABEL_NEW,
                                          reason="The file is not in the RPI folder.", repair=True)
                      for a in candidates if a.file_name in missing]
    return items


def process(conn, storage, account: str, location: EventLocation, event_id: str, comparison: changes.Comparison,
            rpi_root, data_dir, progress=None) -> transfer.TransferResult:
    """Carry out the RPI downloads and removals of `comparison`. Raises `localfiles.LocalFileError` only if the RPI
    folder itself cannot be used; problems with single files are counted."""
    progress = progress or NullProgress()
    if not any(a.led_type == changes.RPI and (a.action in (changes.DOWNLOAD, changes.DELETE) or a.local_status == "Success")
               for a in comparison.assessments):
        return transfer.TransferResult()                       # nothing to do: no folder is even created
    root = localfiles.open_root(rpi_root, data_dir)
    event_dir = localfiles.open_subfolder(root, event_id)
    items = rpi_items(comparison, event_dir)
    if not items:
        return transfer.TransferResult()
    return transfer.run(conn, storage, account, location, event_id, items, operation=OPERATION,
                        folder_for=lambda item: event_dir, existing_folder_for=lambda item: event_dir,
                        table_led_of=lambda item: (None, changes.RPI), progress=progress, listings={})
