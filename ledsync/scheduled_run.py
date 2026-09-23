"""Headless entry point for a scheduled run (BRD Section 22; Desktop BRD Addendum A Section 39.6,
confirmed by the owner 23 September 2026: a scheduled run must work with nobody logged into Windows).

Invoked by Windows Task Scheduler (services/scheduler.py registers it) as
`<python> -m ledsync.scheduled_run`. Deliberately imports NOTHING from pywebview or the Flask web
layer - see main.py's own note on this - so it can run with no desktop session, no browser window,
and no WebView2 runtime present at all.

Runs the exact same Download & Sync + notification pipeline the interactive Dashboard's own
"Download & Sync" button uses (services/downloads.run_job - completely unchanged), once for every
registered event, one after another; one event failing never stops the rest (mirrors the existing
"one bad file/device never stops the others" principle one level up). Guarded by the same
cross-process lock the interactive app takes (services/singleinstance.py), so a scheduled run can
never collide with an already-open interactive session or with a previous scheduled run that is
still going (the "owner confirmed: catch up when next possible" setting on the Task itself can
otherwise fire a second occurrence before a slow first one has finished).
"""

import logging
import sys

from . import APP_NAME, __version__, config, logging_setup
from .db import connect, init_db
from .services import auth, downloads, oplog, singleinstance
from .services import settings as cloud_settings
from .services.progress import Progress
from .services.storage import from_settings

log = logging.getLogger("ledsync")


def _startup_housekeeping(conn) -> None:
    """The same housekeeping the interactive app does at launch (main._seed_and_log_startup) -
    a scheduled run may be the first thing to run after the venue machine sat idle for days."""
    from .main import _prune_logs        # local import: keeps main.py's own webview-lazy-import intact
    auth.seed_admin(conn)
    _prune_logs(conn)


def _run_one_event(cfg, storage_factory, settings, event_id: str) -> None:
    """One event's Download & Sync, exactly as the Dashboard button runs it. Exceptions are caught
    here (not inside downloads.run_job, which already handles its own) only to guarantee that a bug
    completely outside run_job's own defences still can't stop the remaining events."""
    try:
        downloads.run_job(cfg, storage_factory, settings, event_id, Progress(), tz=None,
                          download=True, push=True)
    except Exception:                                          # noqa: BLE001 - next event must still run
        log.exception("Scheduled run: event %s failed unexpectedly", event_id)


def _run(cfg) -> int:
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    try:
        _startup_housekeeping(conn)
        settings = cloud_settings.load_cloud(conn)
        event_ids = [row[0] for row in conn.execute("SELECT event_id FROM events ORDER BY event_id")]
    finally:
        conn.close()

    conn = connect(cfg.db_path)
    try:
        oplog.record(conn, "Scheduled Run", "Started", f"Scheduled run started ({len(event_ids)} event(s)).")
    finally:
        conn.close()

    if not event_ids:
        conn = connect(cfg.db_path)
        try:
            oplog.record(conn, "Scheduled Run", "Success", "No events are registered; nothing to do.")
        finally:
            conn.close()
        return 0
    if not settings.configured:
        conn = connect(cfg.db_path)
        try:
            oplog.record(conn, "Scheduled Run", "Failed",
                         "Cloud Storage is not configured (Settings -> Cloud Storage); no event was processed.")
        finally:
            conn.close()
        return 1

    for event_id in event_ids:
        _run_one_event(cfg, from_settings, settings, event_id)

    conn = connect(cfg.db_path)
    try:
        oplog.record(conn, "Scheduled Run", "Success", f"Scheduled run finished ({len(event_ids)} event(s)).")
    finally:
        conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    cfg = config.load()
    log_path = logging_setup.setup_logging(cfg.data_dir)
    log.info("Scheduled run starting (%s v%s, data dir: %s)", APP_NAME, __version__, cfg.data_dir)
    if log_path:
        log.info("Diagnostic log: %s", log_path)
    try:
        with singleinstance.instance_lock(cfg.data_dir):
            return _run(cfg)
    except singleinstance.AlreadyRunning:
        log.warning("Scheduled run skipped: the application (or another scheduled run) is already "
                   "using this data folder.")
        init_db(cfg.db_path)
        conn = connect(cfg.db_path)
        try:
            oplog.record(conn, "Scheduled Run", "Blocked",
                         "Skipped - the application, or another scheduled run, was already using this data folder.")
        finally:
            conn.close()
        return 0
    except Exception:
        log.exception("Scheduled run failed unexpectedly")
        return 1


if __name__ == "__main__":
    sys.exit(main())
