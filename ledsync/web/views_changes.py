"""Change log and download page (Phases 6 and 7): compare Azure with what has been processed, then download.

CHECK FOR CHANGES only reads and compares (BRD Sections 16-18). DOWNLOAD FILES starts a background job (services/downloads.py)
that downloads the RPI files and the Table / LED files, with progress and Cancel; the page polls it. The result of the last
check is kept in memory (per event) so the page can be reloaded without contacting Azure; it is dropped as soon as the
registration or the local history changes.
"""

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, session, url_for

from .. import APP_NAME, __version__
from ..services import assets, changes, downloads, localfiles, rpi, settings as cloud_settings
from ..services import events as event_service
from ..services import structure
from .app_db import get_db
from .security import login_required
from .views_devices import _load

bp = Blueprint("changes", __name__, url_prefix="/events")

SHOW_LIMIT = 500                      # rows shown per section; the counts always cover everything
NOT_CONFIGURED = ("Cloud storage is not set up yet. Open Settings and enter the storage account name "
                  "and access key first.")


def _reports() -> dict:
    return current_app.extensions["ledsync.change_reports"]


def _jobs() -> downloads.JobRegistry:
    return current_app.extensions["ledsync.jobs"]


def _key(event_id: str) -> str:
    return event_id.casefold()


def _row(a: changes.Assessment, tz) -> dict:
    return dict(path=a.path, table=a.table, led=structure.LED_LABELS.get(a.led_type or "", a.led_type or ""), file=a.file_name,
                cloud_status=a.cloud_status, changed=event_service.format_timestamp(a.cloud_time_text, tz),
                action=a.action, label=a.label, reason=a.reason, revisions=a.revisions)


def _section(comparison: changes.Comparison, action: str, tz) -> dict:
    items = comparison.with_action(action)
    return dict(total=len(items), rows=[_row(a, tz) for a in items[:SHOW_LIMIT]], limit=SHOW_LIMIT)


def _waiting(report: changes.Report, event_id: str, data_dir):
    """(RPI count, Table/LED count, RPI folder, asset folder) of what a download would do, including files missing from disk."""
    db = get_db()
    rpi_folder = cloud_settings.load_rpi_folder(db, data_dir)
    asset_folder = cloud_settings.load_asset_folder(db, data_dir)
    try:
        n_rpi = len(rpi.rpi_items(report.comparison, rpi.event_folder(rpi_folder.effective, event_id)))
        n_assets = len(assets.asset_items(report.comparison, assets.event_folder(asset_folder.effective, event_id)))
    except localfiles.LocalFileError:
        n_rpi = len(rpi.rpi_items(report.comparison))
        n_assets = len(assets.asset_items(report.comparison))
    return n_rpi, n_assets, rpi_folder, asset_folder


@bp.get("/<event_id>/changes")
@login_required
def page(event_id):
    row = _load(event_id)
    tz = current_app.config.get("DISPLAY_TZ")
    data_dir = current_app.config["LEDSYNC"].data_dir
    report = _reports().get(_key(row["event_id"]))
    if report is not None and not changes.is_current(get_db(), report):
        _reports().pop(_key(row["event_id"]), None)        # made for an older registration or local history
        report = None
    job = _jobs().get(row["event_id"])
    ctx = dict(app_name=APP_NAME, version=__version__, username=session.get("user"), event=row, report=None,
               running=bool(job and job.progress.state == "running"), job_summary=_jobs().take_summary(row["event_id"]),
               waiting_total=0)
    ctx["rpi_folder"] = cloud_settings.load_rpi_folder(get_db(), data_dir)
    ctx["asset_folder"] = cloud_settings.load_asset_folder(get_db(), data_dir)
    if report is not None:
        c = report.comparison
        n_rpi, n_assets, ctx["rpi_folder"], ctx["asset_folder"] = _waiting(report, row["event_id"], data_dir)
        ctx.update(report=report, checked=event_service.format_timestamp(report.checked_at.isoformat(), tz),
                   comparison=c, source_name=c.source_name,
                   to_process=dict(total=c.count(changes.DOWNLOAD) + c.count(changes.DELETE),
                                   rows=[_row(a, tz) for a in
                                         (c.with_action(changes.DELETE) + c.with_action(changes.DOWNLOAD))[:SHOW_LIMIT]],
                                   limit=SHOW_LIMIT),
                   done=_section(c, changes.DONE, tz), na=_section(c, changes.NOT_APPLICABLE, tz),
                   n_new=c.count_label(changes.LABEL_NEW), n_updated=c.count_label(changes.LABEL_UPDATED),
                   n_removed=c.count_label(changes.LABEL_REMOVED), n_unlogged=c.count_label(changes.LABEL_NEW_UNLOGGED),
                   skipped=c.skipped[:20], future=report.future_dated, rpi_waiting=n_rpi, asset_waiting=n_assets,
                   waiting_total=n_rpi + n_assets)
    return render_template("event_changes.html", **ctx)


@bp.post("/<event_id>/changes/check")
@login_required
def check(event_id):
    row = _load(event_id)
    db = get_db()
    settings = cloud_settings.load_cloud(db)
    if not settings.configured:
        flash(NOT_CONFIGURED, "error")
        return redirect(url_for("changes.page", event_id=row["event_id"]))
    storage = current_app.extensions["ledsync.storage_factory"](settings)
    try:
        report = changes.check_event(db, storage, settings, row["event_id"])
    except changes.CheckError as err:
        flash(err.message, "error")
        return redirect(url_for("changes.page", event_id=row["event_id"]))
    _reports()[_key(row["event_id"])] = report
    c = report.comparison
    flash(f"Checked {c.total_entries} change log entr{'y' if c.total_entries == 1 else 'ies'}: "
          f"{c.count(changes.DOWNLOAD)} to download, {c.count(changes.DELETE)} to remove, "
          f"{c.count(changes.DONE)} already processed. Nothing was downloaded or changed.", "success")
    if c.azure_note:
        flash(f"Azure's own file list could not be compared: {c.azure_note}", "info")
    return redirect(url_for("changes.page", event_id=row["event_id"]))


@bp.post("/<event_id>/changes/download")
@login_required
def download(event_id):
    """Start the background download of this event's RPI files and Table / LED files."""
    row = _load(event_id)
    back = redirect(url_for("changes.page", event_id=row["event_id"]))
    db = get_db()
    settings = cloud_settings.load_cloud(db)
    if not settings.configured:
        flash(NOT_CONFIGURED, "error")
        return back
    config = current_app.config["LEDSYNC"]
    factory = current_app.extensions["ledsync.storage_factory"]
    tz = current_app.config.get("DISPLAY_TZ")
    reports, key = _reports(), _key(row["event_id"])

    def work(progress):
        return downloads.run_download(config, factory, settings, row["event_id"], progress, tz,
                                      on_report=lambda fresh: reports.__setitem__(key, fresh))

    job = _jobs().start(row["event_id"], work, inline=bool(current_app.config.get("SYNC_JOBS")))
    if job is None:
        flash("A download is already running for this event.", "info")
    return back


@bp.post("/<event_id>/changes/cancel")
@login_required
def cancel(event_id):
    row = _load(event_id)
    job = _jobs().get(row["event_id"])
    if job is not None and job.progress.state == "running":
        job.progress.cancel()
        flash("Cancelling after the current file. Nothing half-written is kept.", "info")
    return redirect(url_for("changes.page", event_id=row["event_id"]))


@bp.get("/<event_id>/changes/progress")
@login_required
def progress(event_id):
    row = _load(event_id)
    job = _jobs().get(row["event_id"])
    return jsonify(job.progress.snapshot() if job is not None else {"state": "idle"})
