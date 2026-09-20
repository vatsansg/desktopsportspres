"""Flask application factory."""

import secrets
import sqlite3

from flask import Flask, abort, current_app, g, render_template, request

from .. import APP_NAME, __version__
from ..config import Config
from ..db import connect


def get_db() -> sqlite3.Connection:
    """Per-request SQLite connection (closed automatically at request end)."""
    if "db" not in g:
        g.db = connect(current_app.config["LEDSYNC"].db_path)
    return g.db


def create_app(config: Config) -> Flask:
    app = Flask(__name__)
    app.config["LEDSYNC"] = config
    # Per-launch key for signing sessions (Phase 1). Never persisted or hard-coded.
    app.config["SECRET_KEY"] = secrets.token_hex(32)
    # Fails CLOSED: every request is refused until the launcher (or a test) sets
    # the hosts it is reachable on.
    app.config["ALLOWED_HOSTS"] = frozenset()

    @app.before_request
    def _reject_foreign_host():
        # The UI is only ever served to our own window on loopback. Rejecting any
        # other Host header blocks DNS-rebinding attacks from a browser on the
        # same machine reaching the local API.
        if request.host not in app.config["ALLOWED_HOSTS"]:
            abort(400)

    @app.teardown_appcontext
    def _close_db(_exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.after_request
    def _security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        )
        return resp

    @app.get("/")
    def index():
        return render_template("index.html", app_name=APP_NAME, version=__version__)

    return app
