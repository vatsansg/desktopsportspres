"""Change log page (Phase 6): read the cloud change log and show what is new, updated or removed.

Nothing is downloaded, deleted or pushed here - this only reads and compares (BRD Sections 16-18).
The result of the last check is kept in memory (per event) so the page can be reloaded without
contacting Azure again; it is rebuilt by the next CHECK FOR CHANGES.
"""

from flask import Blueprint, current_app, flash, redirect, render_template, session, url_for

from .. import APP_NAME, __version__
from ..services import changes, settings as cloud_settings
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


def _key(event_id: str) -> str:
    return event_id.casefold()


def _row(a: changes.Assessment, tz) -> dict:
    return dict(path=a.path, table=a.table, led=structure.LED_LABELS.get(a.led_type or "", ""), file=a.file_name,
                cloud_status=a.cloud_status, changed=event_service.format_timestamp(a.cloud_time_text, tz),
                action=a.action, label=a.label, reason=a.reason, revisions=a.revisions)


def _section(comparison: changes.Comparison, action: str, tz) -> dict:
    items = comparison.with_action(action)
    return dict(total=len(items), rows=[_row(a, tz) for a in items[:SHOW_LIMIT]], limit=SHOW_LIMIT)


@bp.get("/<event_id>/changes")
@login_required
def page(event_id):
    row = _load(event_id)
    tz = current_app.config.get("DISPLAY_TZ")
    report = _reports().get(_key(row["event_id"]))
    ctx = dict(app_name=APP_NAME, version=__version__, username=session.get("user"), event=row, report=None)
    if report is not None:
        c = report.comparison
        ctx.update(report=report, checked=event_service.format_timestamp(report.checked_at.isoformat(), tz),
                   comparison=c, source_name=c.source_name,
                   to_process=dict(total=c.count(changes.DOWNLOAD) + c.count(changes.DELETE),
                                   rows=[_row(a, tz) for a in
                                         (c.with_action(changes.DOWNLOAD) + c.with_action(changes.DELETE))[:SHOW_LIMIT]],
                                   limit=SHOW_LIMIT),
                   done=_section(c, changes.DONE, tz), na=_section(c, changes.NOT_APPLICABLE, tz),
                   n_new=c.count_label(changes.LABEL_NEW), n_updated=c.count_label(changes.LABEL_UPDATED),
                   n_removed=c.count_label(changes.LABEL_REMOVED), skipped=c.skipped[:20])
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
    return redirect(url_for("changes.page", event_id=row["event_id"]))
