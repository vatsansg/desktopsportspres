"""Routes: launch handshake, login/logout, dashboard, change password."""

import hmac

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for,
)

from .. import APP_NAME, __version__
from ..services import auth, events, oplog
from .app_db import get_db
from .security import SessionEpoch, is_authenticated, login_required

bp = Blueprint("views", __name__)


def _throttle() -> auth.LoginThrottle:
    return current_app.extensions["ledsync.throttle"]


def _epoch() -> SessionEpoch:
    return current_app.extensions["ledsync.epoch"]


def _begin_authenticated_session(username: str) -> None:
    """Fresh session (prevents session fixation); keeps only the launch flag and
    stamps the current epoch so a later logout/password change can revoke it."""
    launched = session.get("launch")
    session.clear()
    session["launch"] = launched
    session["user"] = username
    session["epoch"] = _epoch().value


@bp.get("/_launch")
def launch():
    expected = current_app.config.get("LAUNCH_TOKEN")
    sent = request.args.get("t", "")
    if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
        abort(403)
    current_app.config["LAUNCH_TOKEN"] = None  # single use
    session.clear()
    session["launch"] = True
    return redirect(url_for("views.dashboard"))


@bp.get("/")
@login_required
def dashboard():
    event_rows = events.list_events(get_db(), tz=current_app.config.get("DISPLAY_TZ"))
    return render_template(
        "dashboard.html", app_name=APP_NAME, version=__version__, username=session["user"],
        events=event_rows,
    )


@bp.get("/login")
def login():
    if is_authenticated():
        return redirect(url_for("views.dashboard"))
    return render_template("login.html", app_name=APP_NAME, version=__version__)


@bp.post("/login")
def login_post():
    db = get_db()
    wait = _throttle().begin_attempt()  # atomic: checks the lock AND counts this attempt
    if wait:
        oplog.record(db, "Login", "Blocked", "Login attempt refused: too many failed attempts.")
        return render_template(
            "login.html", app_name=APP_NAME, version=__version__,
            error=f"Too many failed attempts. Try again in {wait} seconds.",
        ), 429

    username = request.form.get("username", "")[: auth.MAX_USERNAME_LENGTH + 1]
    password = request.form.get("password", "")[: auth.MAX_PASSWORD_LENGTH + 1]

    if auth.check_credentials(db, username, password):
        _throttle().reset()
        _begin_authenticated_session(auth.get_username(db))
        oplog.record(db, "Login", "Success", "Administrator signed in.")
        return redirect(url_for("views.dashboard"))

    # Neither the attempted username nor password is logged or echoed back.
    oplog.record(db, "Login", "Failed", "Authentication failed.")
    return render_template(
        "login.html", app_name=APP_NAME, version=__version__,
        error="Incorrect username or password.",
    ), 401


@bp.post("/logout")
@login_required
def logout():
    oplog.record(get_db(), "Logout", "Success", "Administrator signed out.")
    _epoch().bump()  # revokes every session cookie issued so far, not just this one
    launched = session.get("launch")
    session.clear()
    session["launch"] = launched
    return redirect(url_for("views.login"))


@bp.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    ctx = dict(app_name=APP_NAME, version=__version__, username=session["user"],
               min_length=auth.MIN_PASSWORD_LENGTH)
    if request.method == "GET":
        return render_template("change_password.html", **ctx)

    db = get_db()
    wait = _throttle().begin_attempt()
    if wait:
        oplog.record(db, "Password Change", "Blocked", "Refused: too many failed attempts.")
        return render_template(
            "change_password.html", error=f"Too many failed attempts. Try again in {wait} seconds.",
            **ctx,
        ), 429

    limit = auth.MAX_PASSWORD_LENGTH + 1
    try:
        auth.change_password(
            db,
            request.form.get("current_password", "")[:limit],
            request.form.get("new_password", "")[:limit],
            request.form.get("confirm_password", "")[:limit],
        )
    except auth.PasswordChangeError as exc:
        if not exc.counts_as_failure:
            # The current password was right; a typo in the new one is not a guess.
            _throttle().reset()
        oplog.record(db, "Password Change", "Failed", str(exc))
        return render_template("change_password.html", error=str(exc), **ctx), 400

    _throttle().reset()
    oplog.record(db, "Password Change", "Success", "Administrator password changed.")
    # Revoke every older session (a stolen cookie dies), then keep THIS operator signed in.
    _epoch().bump()
    _begin_authenticated_session(session["user"])
    flash("Password changed.", "success")
    return redirect(url_for("views.dashboard"))
