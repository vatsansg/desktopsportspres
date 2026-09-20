"""Runs the Flask app on a loopback-only, randomly-assigned port in a background
thread, and shuts it down cleanly. Shared by the launcher and the tests."""

import threading
from dataclasses import dataclass, field

from flask import Flask
from werkzeug.serving import BaseWSGIServer, WSGIRequestHandler, make_server

LOOPBACK = "127.0.0.1"


class _QuietHandler(WSGIRequestHandler):
    def version_string(self) -> str:
        return "ledsync"  # do not advertise Werkzeug/Python versions


@dataclass
class RunningServer:
    server: BaseWSGIServer
    thread: threading.Thread
    port: int
    launch_token: str = field(repr=False)  # never let the one-time token reach a repr/log

    @property
    def url(self) -> str:
        return f"http://{LOOPBACK}:{self.port}/"

    @property
    def launch_url(self) -> str:
        """The one-time URL the app's own window opens (see web/security.py)."""
        return f"{self.url}_launch?t={self.launch_token}"

    def stop(self, timeout: float = 5.0) -> bool:
        """Stop serving and release the port. Returns True if the thread exited."""
        self.server.shutdown()
        self.thread.join(timeout)
        self.server.server_close()
        return not self.thread.is_alive()


def start_server(app: Flask) -> RunningServer:
    server = make_server(LOOPBACK, 0, app, threaded=True, request_handler=_QuietHandler)  # port 0 = OS picks a free port
    port = server.server_port
    # Only the literal loopback address the window uses. "localhost" is deliberately
    # excluded: it can resolve to ::1, where another process could own the same port.
    app.config["ALLOWED_HOSTS"] = frozenset({f"{LOOPBACK}:{port}"})
    thread = threading.Thread(target=server.serve_forever, name="flask-server", daemon=True)
    thread.start()
    return RunningServer(server=server, thread=thread, port=port,
                         launch_token=app.config["LAUNCH_TOKEN"])
