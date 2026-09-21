"""Running a download (RPI files and Table / LED files) in the background, with progress and Cancel.

A download can take minutes (large videos), so it never runs inside a web request: the page starts a job on a worker
thread and polls its progress. The job always begins with a FRESH check of Azure (it never trusts an earlier result),
then downloads the RPI files and the Table / LED files, refreshes `_localchangelog.csv`, and stores a new result for the
page. It uses its own database connection. Only one job runs per event at a time.

The summary of a finished job is stored BEFORE the job's state changes to finished, so a page that sees "finished" always
finds its summary.
"""

import logging
import threading
from dataclasses import dataclass, field

from ..db import connect
from . import assets, changes, localchangelog, localfiles, rpi
from . import settings as cloud_settings
from .progress import Progress
from .storage import EventLocation
from .transfer import TransferResult

log = logging.getLogger(__name__)


def _line(level: str, text: str) -> dict:
    return {"level": level, "text": text}


@dataclass
class Job:
    event_id: str
    progress: Progress
    thread: threading.Thread | None = None
    summary: list[dict] = field(default_factory=list)     # {level, text}; shown once when the job has finished
    seen: bool = False


class JobRegistry:
    """The jobs of this application run, by event (case-insensitive)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    @staticmethod
    def _key(event_id: str) -> str:
        return event_id.casefold()

    def get(self, event_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(self._key(event_id))

    def running(self, event_id: str) -> bool:
        job = self.get(event_id)
        return bool(job and job.progress.state == "running")

    def start(self, event_id: str, work, inline: bool = False) -> Job | None:
        """Start `work(progress) -> list[dict]`. Returns None if a job for this event is already running."""
        with self._lock:
            existing = self._jobs.get(self._key(event_id))
            if existing and existing.progress.state == "running":
                return None
            progress = Progress()
            progress.state = "running"
            job = Job(event_id, progress)
            self._jobs[self._key(event_id)] = job

        def target():
            try:
                job.summary = work(progress)
            except Exception:                                    # noqa: BLE001 - a job must always end in a state
                log.exception("The download job failed")
                job.summary = [_line("error", "The download stopped because of an unexpected problem.")]
                progress.final_state = "error"
            # The summary is in place BEFORE the state says "finished".
            progress.end(progress.final_state, job.summary[0]["text"] if job.summary else "")

        if inline:
            target()
        else:
            job.thread = threading.Thread(target=target, name=f"download-{event_id}", daemon=True)
            job.thread.start()
        return job

    def take_summary(self, event_id: str) -> list[dict]:
        """The summary of a finished job, once (like a flash message); empty otherwise."""
        with self._lock:
            job = self._jobs.get(self._key(event_id))
            if job is None or job.seen or job.progress.state in ("idle", "running"):
                return []
            job.seen = True
            return list(job.summary)


def _describe(result: TransferResult) -> list[dict]:
    if result.failed or result.stopped:
        head = "error"
    elif result.cancelled or result.remaining:
        head = "info"
    else:
        head = "success"
    lines = [_line(head, f"Downloaded {result.downloaded} file(s), removed {result.removed}, failed {result.failed}.")]
    lines += [_line("error", text) for text in result.failures[:10]]
    if len(result.failures) > 10:
        lines.append(_line("error", f"…and {len(result.failures) - 10} more problems (see the exception log)."))
    if result.gone:
        lines.append(_line("info", f"{result.gone} file(s) were downloaded before but are no longer in Azure; "
                                   "they were dropped from the list."))
    if result.stopped:
        lines.append(_line("error", f"Stopped after a connection or permission problem; {result.remaining} file(s) were not "
                                    "tried. Check the connection and download again."))
    elif result.cancelled:
        lines.append(_line("info", f"Cancelled; {result.remaining} file(s) were not downloaded. Nothing half-written was kept."))
    elif result.remaining:
        lines.append(_line("info", f"{result.remaining} more file(s) are waiting. Download again to continue."))
    return lines


def run_download(config, storage_factory, settings, event_id: str, progress: Progress, tz=None, on_report=None) -> list[dict]:
    """The whole job (blocking). Returns the summary lines and sets `progress.final_state`; the registry ends the job."""
    conn = connect(config.db_path)
    try:
        storage = storage_factory(settings)
        try:
            report = changes.check_event(conn, storage, settings, event_id)          # always from a fresh read
        except changes.CheckError as err:
            progress.final_state = "error"
            return [_line("error", err.message)]
        event_id = report.event_id
        location = EventLocation(report.container, report.folder)
        rpi_folder = cloud_settings.load_rpi_folder(conn, config.data_dir).effective
        asset_folder = cloud_settings.load_asset_folder(conn, config.data_dir).effective
        try:
            waiting = (len(rpi.rpi_items(report.comparison, rpi.event_folder(rpi_folder, event_id)))
                       + len(assets.asset_items(report.comparison, assets.event_folder(asset_folder, event_id))))
        except localfiles.LocalFileError as err:
            progress.final_state = "error"
            return [_line("error", str(err))]
        progress.start(waiting)
        result, notes = TransferResult(), []
        for label, engine, folder in (("RPI files", rpi, rpi_folder), ("Files", assets, asset_folder)):
            try:
                result.add(engine.process(conn, storage, settings.account, location, event_id, report.comparison, folder,
                                          config.data_dir, progress))
            except localfiles.LocalFileError as err:
                notes.append(_line("error", f"{label}: {err}"))
                result.failed += 1
            if result.stopped or result.cancelled:
                break
        if result.stopped or result.cancelled:                     # the files not tried include the other engine's
            done = result.downloaded + result.removed + result.failed + result.gone
            result.remaining = max(result.remaining, waiting - done)
        localchangelog.write(config.data_dir, conn, tz)
        if not (result.stopped or result.cancelled):               # a stopped or cancelled job ends at once
            try:
                fresh = changes.check_event(conn, storage, settings, event_id)
                if on_report is not None:
                    on_report(fresh)
            except changes.CheckError:
                pass
        progress.final_state = "cancelled" if result.cancelled else "done"
        return _describe(result) + notes
    finally:
        conn.close()
