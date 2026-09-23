"""Settings routes (BRD Section 14): Cloud Storage and Local Folders (Phase 4/6/7), Download, Scheduling (Phase 12
runs it), Email and Application (Phase 10)."""

import getpass

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from .. import APP_NAME, __version__, config as app_config
from ..services import exceptions, oplog, scheduler, settings as cs
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


# --- Download Settings (BRD 14; Phase 10) -------------------------------------------------------------------------

def _download_page(db, retry_count_typed=None, retry_delay_typed=None, error=None):
    saved = cs.load_download_settings(db)
    data_dir = current_app.config["LEDSYNC"].data_dir
    from ..services import localchangelog
    return render_template("settings_download.html", **_ctx(
        saved=saved, retry_count_typed=(saved.saved_retry_count if retry_count_typed is None else retry_count_typed),
        retry_delay_typed=(saved.saved_retry_delay if retry_delay_typed is None else retry_delay_typed),
        change_log_path=str(data_dir / localchangelog.FILENAME), error=error))


@bp.get("/download")
@login_required
def download():
    return _download_page(get_db())


@bp.post("/download")
@login_required
def download_post():
    db = get_db()
    retry_count_typed = request.form.get("retry_count", "")[:10]
    retry_delay_typed = request.form.get("retry_delay", "")[:10]
    try:
        changed = cs.save_download_settings(db, retry_count_typed, retry_delay_typed)
    except cs.SettingsError as err:
        exceptions.record_rejection(db, "Settings Changed", "Save Download Settings", str(err))
        return _download_page(db, retry_count_typed, retry_delay_typed, str(err)), 400
    flash("Download settings saved." if changed else "Nothing changed \u2014 the settings were already saved.",
          "success" if changed else "info")
    return redirect(url_for("settings.download"))


# --- Scheduling Settings (BRD 14/22; Phase 12 runs the schedule, with nobody needing to be logged in - -------------
# Addendum A 39.6, confirmed 23 Sep 2026) ------------------------------------------------------------------------

def _scheduling_page(db, enabled=None, days=None, time_typed=None, username_typed=None, error=None):
    saved = cs.load_schedule(db)
    default_username = saved.username or getpass.getuser()
    return render_template("settings_scheduling.html", **_ctx(
        saved=saved, day_choices=cs.SCHEDULE_DAYS, enabled=(saved.enabled if enabled is None else enabled),
        days=(list(saved.days) if days is None else days),
        time_typed=(saved.time if time_typed is None else time_typed),
        username_typed=(default_username if username_typed is None else username_typed),
        registered=scheduler.is_registered(), error=error))


@bp.get("/scheduling")
@login_required
def scheduling():
    return _scheduling_page(get_db())


@bp.post("/scheduling")
@login_required
def scheduling_post():
    db = get_db()
    enabled = request.form.get("enabled") == "1"
    days = request.form.getlist("days")
    time_typed = request.form.get("time", "")[:10]
    username_typed = request.form.get("username", "")[:200]
    password = request.form.get("password", "")

    def refused(message: str):
        exceptions.record_rejection(db, "Settings Changed", "Save Scheduling Settings", message)
        return _scheduling_page(db, enabled, days, time_typed, username_typed, message), 400

    try:
        days_value = cs.validate_schedule_days(days)
        time_value = cs.validate_schedule_time(time_typed)
        username_value = cs.validate_schedule_username(username_typed)
    except cs.SettingsError as err:
        return refused(str(err))
    if enabled and (not days_value or not time_value):
        return refused("Choose at least one day and a time before enabling the schedule.")
    if enabled and not username_value:
        return refused("Enter the Windows account to run the schedule as before turning it on.")
    if enabled and not password:
        return refused("Enter the Windows account password before turning the schedule on. It is used once, "
                       "to register the task with Windows, and is never stored by this application.")

    # The real Task Scheduler entry is registered/removed BEFORE the settings are saved: a bad account
    # or password must refuse the whole save, exactly like every other validation failure in this
    # application - never leave the saved settings claiming "enabled" when Windows disagrees. (Review
    # fix: this applied to register() but not unregister() - both are now guarded the same way.)
    cfg = current_app.config["LEDSYNC"]
    if enabled:
        try:
            scheduler.register(username_value, password, tuple(days_value.split(",")), time_value,
                               app_config.PROJECT_ROOT, cfg.data_dir)
        except scheduler.SchedulerError as err:
            return refused(str(err))
    else:
        try:
            scheduler.unregister()
        except scheduler.SchedulerError as err:
            return refused(str(err))

    try:
        changed = cs.save_schedule(db, enabled, days, time_typed, username_typed)
    except cs.SettingsError as err:
        # Review fix: the Task Scheduler side already changed (registered/removed) above, but the
        # database write itself just failed - a genuine SQLite error, not a validation problem. Best
        # effort to put the real Task Scheduler state back the way it was before this request, so the
        # two never disagree; this can only fail if Task Scheduler itself is now also unreachable, in
        # which case the operator already sees a clear error either way.
        if enabled:
            try:
                scheduler.unregister()
            except scheduler.SchedulerError:
                pass
        return refused(str(err))
    flash("Scheduling settings saved." if changed else "Nothing changed \u2014 the settings were already saved.",
          "success" if changed else "info")
    return redirect(url_for("settings.scheduling"))


# --- Email Settings (BRD 14/23; Phase 11 sends the notification) -----------------------------------------------------

def _email_page(db, enabled=None, recipient_typed=None, sender_typed=None, error=None):
    saved = cs.load_email(db)
    return render_template("settings_email.html", **_ctx(
        saved=saved, enabled=(saved.enabled if enabled is None else enabled),
        recipient_typed=cs.redact_if_secret_like(saved.recipient if recipient_typed is None else recipient_typed),
        sender_typed=cs.redact_if_secret_like(saved.sender if sender_typed is None else sender_typed),
        error=error))


@bp.get("/email")
@login_required
def email():
    return _email_page(get_db())


@bp.post("/email")
@login_required
def email_post():
    db = get_db()
    enabled = request.form.get("enabled") == "1"
    recipient_typed = request.form.get("recipient", "")[:2000]
    sender_typed = request.form.get("sender", "")[:200]
    connection_typed = request.form.get("connection_string", "")[:2000]
    try:
        changed = cs.save_email(db, enabled, recipient_typed, sender_typed, connection_typed)
    except cs.SettingsError as err:
        exceptions.record_rejection(db, "Settings Changed", "Save Email Settings", str(err))
        return _email_page(db, enabled, recipient_typed, sender_typed, str(err)), 400
    flash("Email settings saved." if changed else "Nothing changed \u2014 the settings were already saved.",
          "success" if changed else "info")
    return redirect(url_for("settings.email"))


# --- Application Settings (BRD 14; locations are read-only - Phase 13 owns installation/upgrade) ---------------------

def _application_page(db, retention_typed=None, error=None):
    from .. import logging_setup
    cfg = current_app.config["LEDSYNC"]
    saved = cs.load_log_retention(db)
    return render_template("settings_application.html", **_ctx(
        saved=saved, retention_typed=(saved.saved if retention_typed is None else retention_typed), error=error,
        database_path=str(cfg.db_path), app_log_path=str(logging_setup.log_dir(cfg.data_dir) / logging_setup.LOG_FILENAME)))


@bp.get("/application")
@login_required
def application():
    return _application_page(get_db())


@bp.post("/application")
@login_required
def application_post():
    db = get_db()
    retention_typed = request.form.get("retention", "")[:10]
    try:
        changed = cs.save_log_retention(db, retention_typed)
    except cs.SettingsError as err:
        exceptions.record_rejection(db, "Settings Changed", "Save Application Settings", str(err))
        return _application_page(db, retention_typed, str(err)), 400
    flash("Application settings saved." if changed else "Nothing changed \u2014 the settings were already saved.",
          "success" if changed else "info")
    return redirect(url_for("settings.application"))
