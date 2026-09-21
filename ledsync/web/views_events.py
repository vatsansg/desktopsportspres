"""Event registration routes (BRD Sections 8 and 9): Add New Event and Re-register."""

import hmac
from pathlib import PureWindowsPath

from flask import (
    Blueprint, current_app, flash, redirect, render_template, request, session, url_for,
)

from .. import APP_NAME, __version__
from ..services import registration as reg
from .app_db import get_db
from .security import login_required

bp = Blueprint("events", __name__, url_prefix="/events")

SESSION_KEY = "rereg"
EXPIRED = "That re-registration request has expired or was already completed. Add the event again."
STALE = "That screen was out of date (another registration has been started since). Start again."


def _ctx(**extra):
    return dict(app_name=APP_NAME, version=__version__, username=session.get("user"), **extra)


def _pending() -> reg.PendingReregistrations:
    return current_app.extensions["ledsync.pending"]


def source_label(filename: str | None) -> str:
    """Where the configuration came from, for the record. The client-supplied file name is
    untrusted, so only a sanitised base name is kept."""
    name = PureWindowsPath(filename or "").name
    name = "".join(c if (c.isalnum() or c in "._- ") else "_" for c in name)[:100].strip()
    return f"Local test file: {name}" if name else "Local test file"


def _read_upload() -> tuple[bytes, str]:
    upload = request.files.get("config_file")
    if upload is None or not upload.filename:
        raise reg.InputError("Choose the event's _GUID.json file.")
    data = upload.stream.read(reg.MAX_CONFIG_BYTES + 1)
    return data, source_label(upload.filename)


@bp.get("/new")
@login_required
def new():
    return render_template("event_new.html", **_ctx(entered_id=""))


@bp.post("/new")
@login_required
def new_post():
    db = get_db()
    entered = request.form.get("event_id", "")
    event_id = None
    source = None
    try:
        event_id = reg.validate_event_id_input(entered)
        data, source = _read_upload()
        config = reg.parse_event_config(data)
        outcome = reg.register_event(db, event_id, config, source)
    except reg.GuidMismatch as err:
        reg.log_rejection(db, err, "Register Event", event_id, source)
        # Keep the rejected file server-side (briefly) so the operator can confirm the new
        # GUID. Any earlier pending request from this session is dropped, never orphaned.
        _pending().discard(session.get(SESSION_KEY))
        session[SESSION_KEY] = _pending().add(event_id, config, source, err.recorded)
        return redirect(url_for("events.reregister"))
    except reg.RegistrationError as err:
        reg.log_rejection(db, err, "Register Event", event_id, source)
        return render_template("event_new.html", **_ctx(entered_id=entered.strip()[:60],
                                                        error=err.message)), 400

    if outcome == "already_registered":
        flash(f"Event {event_id} is already registered with this GUID. Nothing changed.", "info")
    else:
        flash(f"Event {event_id} ({config.event_name}) registered.", "success")
    return redirect(url_for("views.dashboard"))


def _current_token() -> str | None:
    return session.get(SESSION_KEY)


def _current_pending():
    return _pending().get(_current_token())


def _expired():
    session.pop(SESSION_KEY, None)
    flash(EXPIRED, "error")
    return redirect(url_for("views.dashboard"))


def _form_matches_current_pending() -> bool:
    """The form must be the one for the request the session currently holds. Without this,
    a second browser tab showing an older request could act on a newer one."""
    sent, current = request.form.get("pending_token", ""), _current_token() or ""
    return bool(current) and hmac.compare_digest(sent.encode(), current.encode())


@bp.get("/reregister")
@login_required
def reregister():
    pending = _current_pending()
    if pending is None:
        return _expired()
    return render_template("event_reregister.html",
                           **_ctx(pending=pending, token=_current_token(),
                                  file_guid_tail=pending.config.guid[-6:]))


@bp.post("/reregister")
@login_required
def reregister_post():
    pending = _current_pending()
    if pending is None:
        return _expired()
    if not _form_matches_current_pending():
        flash(STALE, "error")
        return redirect(url_for("events.reregister"))

    db = get_db()
    try:
        reg.reregister_event(db, pending.event_id, pending.config,
                             request.form.get("pasted_guid", "")[:80], pending.source)
    except reg.RegistrationError as err:
        reg.log_rejection(db, err, "Re-register Event", pending.event_id, pending.source)
        return render_template("event_reregister.html",
                               **_ctx(pending=pending, token=_current_token(), error=err.message,
                                      file_guid_tail=pending.config.guid[-6:])), 400

    _pending().discard(session.pop(SESSION_KEY, None))
    flash(f"Event {pending.event_id} ({pending.config.event_name}) re-registered with the new GUID.",
          "success")
    return redirect(url_for("views.dashboard"))


@bp.post("/reregister/cancel")
@login_required
def reregister_cancel():
    if _current_pending() is None:
        return _expired()
    if not _form_matches_current_pending():
        flash(STALE, "error")
        return redirect(url_for("events.reregister"))
    _pending().discard(session.pop(SESSION_KEY, None))
    flash("Re-registration cancelled. The event was not changed.", "info")
    return redirect(url_for("views.dashboard"))
