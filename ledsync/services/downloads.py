"""Running Download and / or Sync (RPI files, Table / LED files, and pushing to the LED devices) in the background, with
progress and Cancel.

A run can take minutes (large videos, several devices), so it never runs inside a web request: the page starts a job on a
worker thread and polls its progress. A download always begins with a FRESH check of Azure (it never trusts an earlier
result), then downloads the RPI files and the Table / LED files; a sync then pushes what was downloaded to the mapped LED
folders (it needs no Azure at all when only synchronising). Afterwards `_localchangelog.csv` and the event status are
refreshed. The job uses its own database connection. Only one job runs per event at a time.

The summary of a finished job is stored BEFORE the job's state changes to finished, so a page that sees "finished" always
finds its summary.
"""

import logging
import threading
from dataclasses import dataclass, field

from ..db import connect
from . import assets, changes, eventstatus, localchangelog, localfiles, oplog, rpi, sync
from . import settings as cloud_settings
from . import transfer
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
    kind: str = "download"                                # download | sync | both
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

    def start(self, event_id: str, work, inline: bool = False, kind: str = "download") -> Job | None:
        """Start `work(progress) -> list[dict]`. Returns None if a job for this event is already running."""
        with self._lock:
            existing = self._jobs.get(self._key(event_id))
            if existing and existing.progress.state == "running":
                return None
            progress = Progress()
            progress.state = "running"
            job = Job(event_id, progress, kind)
            self._jobs[self._key(event_id)] = job

        def target():
            try:
                job.summary = work(progress)
            except Exception:                                    # noqa: BLE001 - a job must always end in a state
                log.exception("The job failed")
                job.summary = [_line("error", "The run stopped because of an unexpected problem.")]
                progress.final_state = "error"
            # The summary is in place BEFORE the state says "finished".
            progress.end(progress.final_state, job.summary[0]["text"] if job.summary else "")

        if inline:
            target()
        else:
            job.thread = threading.Thread(target=target, name=f"{kind}-{event_id}", daemon=True)
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

    def running_snapshots(self) -> list[dict]:
        """The progress of every running job, for the dashboard's Operation Status."""
        with self._lock:
            jobs = list(self._jobs.values())
        return [dict(job.progress.snapshot(), event_id=job.event_id, kind=job.kind)
                for job in jobs if job.progress.state == "running"]

    def take_all_summaries(self) -> list[dict]:
        """Summaries of every finished job not yet shown: [{event_id, lines}]."""
        with self._lock:
            jobs = [job for job in self._jobs.values() if not job.seen and job.progress.state not in ("idle", "running")]
            for job in jobs:
                job.seen = True
            return [{"event_id": job.event_id, "lines": list(job.summary)} for job in jobs]


def _describe_download(result: TransferResult) -> list[dict]:
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


# kept under its Phase 7 name for callers that only download
_describe = _describe_download


def _describe_sync(result: sync.SyncResult) -> list[dict]:
    head = "error" if result.failed else ("info" if result.cancelled or result.remaining else "success")
    lines = [_line(head, f"Synchronised {result.pushed} file(s) to the LED devices, removed {result.removed}, "
                         f"failed {result.failed}.")]
    if result.unreachable:
        lines.append(_line("error", "Could not use the device folder for: " + ", ".join(result.unreachable) + "."))
    lines += [_line("error", text) for text in result.failures[:10]]
    if len(result.failures) > 10:
        lines.append(_line("error", f"…and {len(result.failures) - 10} more problems (see the exception log)."))
    if result.unmapped:
        lines.append(_line("info", "No device folder is set for: " + ", ".join(result.unmapped) +
                           ". Set it on the event's Device Mapping page."))
    if result.cancelled:
        lines.append(_line("info", f"Cancelled; {result.remaining} file(s) were not sent. Nothing half-written was left on a device."))
    elif result.remaining:
        lines.append(_line("info", f"{result.remaining} more file(s) are waiting. Synchronise again to continue."))
    return lines


def _run_record(config, operation: str, status: str, message: str, event_id: str) -> None:
    """One run-level row in the operational log (BRD 25), on its own short connection. Never raises."""
    try:
        conn = connect(config.db_path)
        try:
            oplog.record(conn, operation, status, message, event_id)
        finally:
            conn.close()
    except Exception:                                            # noqa: BLE001 - the log must never stop a run
        log.exception("Could not write the run-level log row")


def run_job(config, storage_factory, settings, event_id: str, progress: Progress, tz=None, on_report=None, *,
            download: bool = True, push: bool = True, checker=None) -> list[dict]:
    """The whole job (blocking), bracketed by a Started row and a finished row in the operational log (the finished row
    carries the counts). Returns the summary lines and sets `progress.final_state`; the registry ends the job."""
    label = "Download & Sync" if download and push else ("Download Files" if download else "Sync Files")
    _run_record(config, label, "Started", f"{label} started.", event_id)
    try:
        lines = _run_job(config, storage_factory, settings, event_id, progress, tz, on_report, download=download, push=push,
                         checker=checker)
    except BaseException:
        _run_record(config, label, "Failed", "The run stopped because of an unexpected problem.", event_id)
        raise
    state = progress.final_state
    status = "Cancelled" if state == "cancelled" else ("Failed" if state == "error" or progress.errors else "Success")
    _run_record(config, label, status, f"Identified {progress.identified}, downloaded {progress.downloaded}, "
                                       f"synchronised {progress.synchronised}, errors {progress.errors}.", event_id)
    return lines


def _run_job(config, storage_factory, settings, event_id: str, progress: Progress, tz=None, on_report=None, *,
             download: bool = True, push: bool = True, checker=None) -> list[dict]:
    conn = connect(config.db_path)
    try:
        # Settings -> Download (Phase 10) governs both the download and push retry behaviour. Read once here and
        # passed explicitly to every engine below - an ordinary local value, never a shared mutable global - so two
        # jobs for different events running at the same time on their own threads can never see, or leave behind,
        # each other's retry configuration.
        retry_settings = cloud_settings.load_download_settings(conn)
        retries, delay = retry_settings.retry_count, retry_settings.retry_delay
        progress.phase = "Checking"
        canonical = conn.execute("SELECT event_id FROM events WHERE event_id = ? COLLATE NOCASE", (event_id,)).fetchone()
        if canonical is None:
            progress.final_state = "error"
            return [_line("error", "That event is not registered.")]
        event_id = canonical["event_id"]
        asset_folder = cloud_settings.load_asset_folder(conn, config.data_dir).effective
        lines: list[dict] = []
        stopped = cancelled = False

        if download:
            storage = storage_factory(settings)
            try:
                report = changes.check_event(conn, storage, settings, event_id)      # always from a fresh read
            except changes.CheckError as err:
                progress.final_state = "error"
                return [_line("error", err.message)]
            location = EventLocation(report.container, report.folder)
            rpi_folder = cloud_settings.load_rpi_folder(conn, config.data_dir).effective
            try:
                waiting = (len(rpi.rpi_items(report.comparison, rpi.event_folder(rpi_folder, event_id)))
                           + len(assets.asset_items(report.comparison, assets.event_folder(asset_folder, event_id))))
            except localfiles.LocalFileError as err:
                progress.final_state = "error"
                return [_line("error", str(err))]
            progress.identify(waiting)
            progress.set_phase("Downloading", waiting)
            result, notes = TransferResult(), []
            for label, engine, folder in (("RPI files", rpi, rpi_folder), ("Files", assets, asset_folder)):
                try:
                    result.add(engine.process(conn, storage, settings.account, location, event_id, report.comparison, folder,
                                              config.data_dir, progress, retries=retries, delay=delay))
                except localfiles.LocalFileError as err:
                    notes.append(_line("error", f"{label}: {err}"))
                    result.failed += 1
                    progress.tally_error()
                if result.stopped or result.cancelled:
                    break
            stopped, cancelled = result.stopped, result.cancelled
            if stopped or cancelled:                               # the files not tried include the other engine's
                done = result.downloaded + result.removed + result.failed + result.gone
                result.remaining = max(result.remaining, waiting - done)
            lines += _describe_download(result) + notes

        if push and not (stopped or cancelled):
            try:
                items, unmapped = sync.plan(conn, event_id, assets.event_folder(asset_folder, event_id))
            except localfiles.LocalFileError as err:
                items, unmapped = [], []
                lines.append(_line("error", str(err)))
            progress.identify(len(items))
            progress.set_phase("Synchronising", len(items))
            protected = (str(asset_folder), str(cloud_settings.load_rpi_folder(conn, config.data_dir).effective))
            sresult = sync.process(conn, event_id, items, config.data_dir, progress, checker, protected, retries, delay)
            sresult.unmapped = unmapped
            cancelled = cancelled or sresult.cancelled
            lines += _describe_sync(sresult)
        elif push:
            lines.append(_line("info", "Synchronisation was not started because the download did not finish."))

        eventstatus.refresh(conn, event_id)
        localchangelog.write(config.data_dir, conn, tz)
        if download and not (stopped or cancelled):                # a stopped or cancelled job ends at once
            try:
                fresh = changes.check_event(conn, storage, settings, event_id)
                if on_report is not None:
                    on_report(fresh)
            except changes.CheckError:
                pass
        progress.final_state = "cancelled" if cancelled else "done"
        return lines
    finally:
        conn.close()


def run_download(config, storage_factory, settings, event_id: str, progress: Progress, tz=None, on_report=None) -> list[dict]:
    """Download only (the Change Log page's DOWNLOAD FILES)."""
    return run_job(config, storage_factory, settings, event_id, progress, tz, on_report, download=True, push=False)
