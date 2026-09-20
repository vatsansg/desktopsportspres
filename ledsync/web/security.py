"""Request-level security for the loopback UI server.

Layers, in order:
  1. Host-header check (in app.py) - blocks DNS rebinding. Only `127.0.0.1:<port>`.
  2. Launch gate - only the window this process opened may talk to the server.
     The launcher opens `/_launch?t=<one-time token>`; the token is exchanged
     for a signed, HttpOnly, SameSite=Strict session cookie and then destroyed.
     A hostile web page or another local process that guesses the port has no
     token and no cookie, so gets 403 on every page and endpoint. (Static
     CSS/JS is exempt so that the branded error page can render; it is public
     source, not data.)
  3. CSRF token on every state-changing request.
  4. Session epoch - sessions are signed cookies, so they cannot be deleted
     server-side; instead each carries the epoch it was issued in, and bumping
     the epoch (on logout / password change) invalidates every older cookie,
     including one an attacker may have captured.
"""

import hmac
import secrets
import threading
from functools import wraps

from flask import Flask, abort, redirect, request, session, url_for

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LAUNCH_ENDPOINT = "views.launch"
STATIC_ENDPOINT = "static"


class SessionEpoch:
    def __init__(self):
        self._value = 0
        self._lock = threading.Lock()

    @property
    def value(self) -> int:
        with self._lock:
            return self._value

    def bump(self) -> int:
        with self._lock:
            self._value += 1
            return self._value


def csrf_token() -> str:
    token = session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf"] = token
    return token


def _epoch(app_or_ctx) -> SessionEpoch:
    return app_or_ctx.extensions["ledsync.epoch"]


def is_authenticated() -> bool:
    from flask import current_app

    return bool(session.get("user")) and session.get("epoch") == _epoch(current_app).value


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_authenticated():
            return redirect(url_for("views.login"))
        return view(*args, **kwargs)

    return wrapped


def install(app: Flask) -> None:
    app.extensions["ledsync.epoch"] = SessionEpoch()

    @app.before_request
    def _launch_gate_and_csrf():
        if request.endpoint in (LAUNCH_ENDPOINT, STATIC_ENDPOINT):
            return None
        if not session.get("launch"):
            abort(403)
        if request.method not in SAFE_METHODS:
            sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
            expected = session.get("csrf", "")
            if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
                abort(403)
        return None

    app.jinja_env.globals["csrf_token"] = csrf_token
