"""Download & Sync and Synchronise routes (Phase 8): start, cancel and watch background runs from the Dashboard.

DOWNLOAD & SYNC (BRD Section 15) runs the whole flow for one event: check Azure, download what is new, then push it to the mapped
LED folders. It is also possible to only push (from the Change Log page). The run is a background job (services/downloads.py); the
Dashboard's Operation Status polls it.
"""

from flask import Blueprint, current_app, flash, jsonify, redirect, url_for

from ..services import downloads, settings as cloud_settings
from .app_db import get_db
from .security import login_required
from .views_changes import NOT_CONFIGURED, _reports, _key
from .views_devices import _load

bp = Blueprint("sync", __name__)


def _jobs() -> downloads.JobRegistry:
    return current_app.extensions["ledsync.jobs"]


def _start(row, *, download: bool, push: bool):
    """Start a background run for an event; returns a redirect target name or None if it could not start."""
    db = get_db()
    settings = cloud_settings.load_cloud(db)
    if download and not settings.configured:
        flash(NOT_CONFIGURED, "error")
        return False
    config = current_app.config["LEDSYNC"]
    factory = current_app.extensions["ledsync.storage_factory"]
    tz = current_app.config.get("DISPLAY_TZ")
    checker = current_app.extensions["ledsync.checker"]
    reports, key = _reports(), _key(row["event_id"])

    def work(progress):
        return downloads.run_job(config, factory, settings, row["event_id"], progress, tz,
                                 on_report=lambda fresh: reports.__setitem__(key, fresh),
                                 download=download, push=push, checker=checker)

    kind = "both" if download and push else ("download" if download else "sync")
    if _jobs().start(row["event_id"], work, inline=bool(current_app.config.get("SYNC_JOBS")), kind=kind) is None:
        flash("A run is already in progress for this event.", "info")
    return True


@bp.post("/events/<event_id>/sync")
@login_required
def start(event_id):
    """DOWNLOAD & SYNC for one event (from the Dashboard)."""
    row = _load(event_id)
    _start(row, download=True, push=True)
    return redirect(url_for("views.dashboard"))


@bp.post("/events/<event_id>/sync/push")
@login_required
def push_only(event_id):
    """Send what is already downloaded to the LED devices (from the Change Log page)."""
    row = _load(event_id)
    _start(row, download=False, push=True)
    return redirect(url_for("changes.page", event_id=row["event_id"]))


@bp.post("/events/<event_id>/sync/cancel")
@login_required
def cancel(event_id):
    row = _load(event_id)
    job = _jobs().get(row["event_id"])
    if job is not None and job.progress.state == "running":
        job.progress.cancel()
        flash("Cancelling after the current file. Nothing half-written is kept.", "info")
    return redirect(url_for("views.dashboard"))


@bp.get("/operations/progress")
@login_required
def operations():
    """Every running operation, for the Dashboard's Operation Status."""
    return jsonify(jobs=_jobs().running_snapshots())
