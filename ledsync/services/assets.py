"""Downloading the event's Table / LED files (Phase 7, BRD Sections 15, 19).

Local layout (owner decision 21/09/26): `<asset folder>\\<Event ID>\\Table N\\<LED type>\\<file>` with LED type folders named
Inner, Outer and Main LED, mirroring the event configuration and the Azure layout. The asset folder is a Setting (default:
`Events` inside the application data folder). Only tables and LED types the event actually enables ever get a folder.

Which files: those the comparison marked DOWNLOAD (new or updated in the change log, or present in Azure but missing from the
log), those removed in the cloud (their local copy is deleted), and files the history says were downloaded but are no
longer on disk. The download itself is the shared loop in `transfer.py` (streamed, verified, atomic, cancellable).
"""

import dataclasses
import time
from pathlib import Path

from . import changes, localfiles, structure, transfer
from .progress import NullProgress
from .storage import EventLocation

OPERATION = "Asset Download"


def event_folder(asset_root, event_id: str) -> Path:
    """Where this event's assets go (no disk access)."""
    return localfiles.subfolder_path(asset_root, event_id)


def destination(event_dir, item: changes.Assessment) -> Path:
    """`<event folder>\\Table N\\<LED type>` for an item (no disk access)."""
    return localfiles.subfolder_path(event_dir, f"Table {item.table}", structure.LED_LABELS[item.led_type])


def asset_items(comparison: changes.Comparison, event_dir=None, budget: float | None = None) -> list[changes.Assessment]:
    """What an asset run would do; with `event_dir`, files missing from disk that the history says were downloaded too.
    `budget` (seconds) bounds the disk checks in total, for a page that must not wait on a slow network folder."""
    deadline = None if budget is None else time.monotonic() + budget
    items = [a for a in comparison.assessments if a.table is not None and a.action in (changes.DOWNLOAD, changes.DELETE)]
    if event_dir is not None:
        by_folder: dict = {}
        for a in comparison.assessments:
            if (a.table is not None and a.action == changes.DONE and a.local_status == "Success"
                    and a.cloud_status in ("New", "Updated")):
                by_folder.setdefault(destination(event_dir, a), []).append(a)
        for folder, group in by_folder.items():
            left = 5.0 if deadline is None else deadline - time.monotonic()
            if left <= 0:
                break                                                     # out of time: show what is certain
            missing = localfiles.missing_files(folder, [a.file_name for a in group], min(5.0, max(left, 0.5)))
            if missing is not None:
                items += [dataclasses.replace(a, action=changes.DOWNLOAD, label=changes.LABEL_NEW,
                                              reason="The file is not in the local folder.", repair=True)
                          for a in group if a.file_name in missing]
    return items


def process(conn, storage, account: str, location: EventLocation, event_id: str, comparison: changes.Comparison,
            asset_root, data_dir, progress=None, *, retries=None, delay=None) -> transfer.TransferResult:
    """Carry out the asset downloads and removals of `comparison`. Raises `localfiles.LocalFileError` only if the asset
    folder itself cannot be used; problems with single files are counted."""
    progress = progress or NullProgress()
    if not any(a.table is not None and (a.action in (changes.DOWNLOAD, changes.DELETE) or a.local_status == "Success")
               for a in comparison.assessments):
        return transfer.TransferResult()                       # nothing to do: no folder is even created
    root = localfiles.open_root(asset_root, data_dir)
    event_dir = localfiles.open_subfolder(root, event_id)
    items = asset_items(comparison, event_dir)
    if not items:
        return transfer.TransferResult()

    def existing(item):
        return localfiles.existing_subfolder(event_dir, f"Table {item.table}", structure.LED_LABELS[item.led_type])

    return transfer.run(conn, storage, account, location, event_id, items, operation=OPERATION,
                        folder_for=lambda item: localfiles.open_subfolder(event_dir, f"Table {item.table}",
                                                                          structure.LED_LABELS[item.led_type]),
                        existing_folder_for=existing, table_led_of=lambda item: (item.table, item.led_type),
                        progress=progress, listings={}, retries=retries, delay=delay)
