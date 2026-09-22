"""Settings routes. Phase 4: the Cloud Storage section (BRD Section 14)."""

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from .. import APP_NAME, __version__
from ..services import exceptions, oplog, settings as cs
from ..services.storage import StorageError
from .app_db import get_db
from .security import login_required

bp = Blueprint("settings", __name__, url_prefix="/settings")


def _ctx(**extra):
    return dict(app_name=APP_NAME, version=__version__, username=session.get("user"), **extra)


def _view_model(saved: cs.CloudSettings, **extra):
    """Pages get a KEY-LESS view of the settings, and typed text is never echoed if it is key-shaped
    (a key pasted into the wrong field must not be reflected into the page)."""
    account = cs.redact_if_secret_like(extra.pop("account", saved.account))
    container = cs.redact_if_secret_like(extra.pop("container", saved.container))
    return _ctx(saved=saved.public(), account=account, container=container, **extra)


@bp.get("")
@login_required
def index():
    return redirect(url_for("settings.cloud"))


@bp.get("/cloud")
@login_required
def cloud():
    return render_template("settings_cloud.html", **_view_model(cs.load_cloud(get_db())))


def _folders_page(db, data_dir, rpi_typed=None, asset_typed=None, error=None):
    rpi_saved = cs.load_rpi_folder(db, data_dir)
    asset_saved = cs.load_asset_folder(db, data_dir)
    return render_template("settings_folders.html", **_ctx(
        folder=rpi_saved, asset=asset_saved, typed=rpi_saved.saved if rpi_typed is None else rpi_typed,
        asset_typed=asset_saved.saved if asset_typed is None else asset_typed, error=error))


@bp.get("/folders")
@login_required
def folders():
    return _folders_page(get_db(), current_app.config["LEDSYNC"].data_dir)


@bp.post("/folders")
@login_required
def folders_post():
    db = get_db()
    data_dir = current_app.config["LEDSYNC"].data_dir
    rpi_typed = request.form.get("rpi_folder", "")[:1000]
    asset_typed = request.form.get("asset_folder", "")[:1000]
    try:
        changed = cs.save_folders(db, rpi_typed, asset_typed, data_dir)
    except cs.SettingsError as err:
        exceptions.record_rejection(db, "Settings Changed", "Save Folder Settings", str(err))
        return _folders_page(db, data_dir, rpi_typed, asset_typed, str(err)), 400
    flash("Local folder settings saved." if changed else "Nothing changed \u2014 the settings were already saved.",
          "success" if changed else "info")
    return redirect(url_for("settings.folders"))


@bp.post("/cloud")
@login_required
def cloud_post():
    db = get_db()
    saved = cs.load_cloud(db)
    account = request.form.get("account", "")
    container = request.form.get("container", "")
    new_key = request.form.get("access_key", "")
    action = request.form.get("action", "save")

    if action == "test":
        return _test_connection(db, saved, account, container, new_key)

    try:
        changed = cs.save_cloud(db, account, container, new_key)
    except cs.SettingsError as err:
        exceptions.record_rejection(db, "Settings Changed", "Save Cloud Settings", str(err))
        return render_template("settings_cloud.html", **_view_model(
            saved, account=account.strip()[:60], container=container.strip()[:70], error=str(err))), 400
    flash("Cloud storage settings saved." if changed else "Nothing changed \u2014 the settings were already saved.",
          "success" if changed else "info")
    return redirect(url_for("settings.cloud"))


def _test_connection(db, saved, account, container, new_key):
    """Try the connection with what is on the form (falling back to the saved key) WITHOUT
    saving anything. Read-only: it only lists the containers."""
    try:
        candidate = cs.CloudSettings(
            account=cs.validate_account(account),
            container=cs.validate_container(container),
            access_key=cs.validate_key(new_key) if new_key.strip() else saved.access_key,
        )
        if not candidate.access_key:
            raise cs.SettingsError("Enter the access key to test the connection.")
    except cs.SettingsError as err:
        return render_template("settings_cloud.html", **_view_model(
            saved, account=account.strip()[:60], container=container.strip()[:70], error=str(err))), 400

    try:
        report = current_app.extensions["ledsync.storage_factory"](candidate).test_connection(candidate.container)
    except StorageError as err:
        oplog.record(db, "Cloud Storage Test", "Failed", err.message)
        exceptions.record(db, err.category, "Test Connection", err.message,
                          source=f"Azure Storage: {candidate.account}")
        return render_template("settings_cloud.html", **_view_model(
            saved, account=candidate.account, container=candidate.container, error=err.message)), 400

    years = ", ".join(report.year_containers) or "none"
    message = f"Connected to storage account {report.account}. Year containers found: {years}."
    if report.preferred_container_found is False:
        message += f" Container {candidate.container} was not found."
    elif report.preferred_container_found:
        message += f" Container {candidate.container} found."
    message += " Nothing has been saved" + (
        " \u2014 type the key again and press Save to keep these settings." if new_key.strip() else ".")
    oplog.record(db, "Cloud Storage Test", "Success", f"Connected to {report.account} ({len(report.containers)} container(s)).")
    exceptions.resolve_matching(db, None, "Test Connection", source=f"Azure Storage: {candidate.account}")
    db.commit()
    return render_template("settings_cloud.html", **_view_model(
        saved, account=candidate.account, container=candidate.container, notice=message)), 200
