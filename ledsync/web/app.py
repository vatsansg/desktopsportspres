"""Flask application factory."""

import secrets

from flask import Flask, abort, render_template, request

from .. import APP_NAME, __version__
from ..config import Config
from ..services.auth import LoginThrottle
from . import security
from .app_db import close_db, get_db  # noqa: F401  (get_db re-exported for callers/tests)
from .views import bp


# Plain, non-technical wording; nothing internal is revealed.
DEFAULT_ERROR = ("Something Went Wrong", "That request could not be completed. Go back and try again.")
ERROR_PAGES = {
    403: ("Session Not Valid",
          "This window is no longer connected to the application. "
          "Close the application and start it again."),
    404: ("Page Not Found", "That page does not exist. Go back to the dashboard."),
    405: DEFAULT_ERROR,
    413: ("Request Too Large", "The information sent was too large. Go back and shorten it."),
    500: ("Something Went Wrong", "The application hit an unexpected problem. Go back and try again; if it continues, restart the application."),
}


def create_app(config: Config) -> Flask:
    app = Flask(__name__)
    app.config["LEDSYNC"] = config
    # Per-launch key for signing sessions. Never persisted or hard-coded.
    app.config["SECRET_KEY"] = secrets.token_hex(32)
    # One-time token the launcher exchanges for the session cookie (security.py).
    app.config["LAUNCH_TOKEN"] = secrets.token_urlsafe(32)
    # Fails CLOSED: every request is refused until the launcher (or a test) sets
    # the hosts it is reachable on.
    app.config["ALLOWED_HOSTS"] = frozenset()

    app.config.update(
        SESSION_COOKIE_NAME="ledsync_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        # Not Secure: the server is plain HTTP on loopback by design.
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    # tzinfo used to display timestamps; None = this machine's local time (owner decision).
    app.config["DISPLAY_TZ"] = None
    app.extensions["ledsync.throttle"] = LoginThrottle()

    @app.before_request
    def _reject_foreign_host():
        # The UI is only ever served to our own window on loopback. Rejecting any
        # other Host header blocks DNS-rebinding attacks from a browser on the
        # same machine reaching the local API.
        if request.host not in app.config["ALLOWED_HOSTS"]:
            abort(400)

    security.install(app)  # registered after the host check, so it runs after it
    app.teardown_appcontext(close_db)
    app.register_blueprint(bp)

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(405)
    @app.errorhandler(413)
    @app.errorhandler(500)
    def _branded_error(err):
        title, message = ERROR_PAGES.get(err.code, DEFAULT_ERROR)
        return render_template(
            "error.html", app_name=APP_NAME, version=__version__,
            code=err.code, title=title, message=message,
        ), err.code

    @app.after_request
    def _security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'none'"
        )
        return resp

    return app
