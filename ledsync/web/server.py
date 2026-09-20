"""Runs the Flask app on a loopback-only, randomly-assigned port in a background
thread, and shuts it down cleanly. Shared by the launcher and the tests."""

import threading
from dataclasses import dataclass

from flask import Flask
from werkzeug.serving import BaseWSGIServer, make_server

LOOPBACK = "127.0.0.1"


@dataclass
class RunningServer:
    server: BaseWSGIServer
    thread: threading.Thread
    port: int

    @property
    def url(self) -> str:
        return f"http://{LOOPBACK}:{self.port}/"

    def stop(self, timeout: float = 5.0) -> bool:
        """Stop serving and release the port. Returns True if the thread exited."""
        self.server.shutdown()
        self.thread.join(timeout)
        self.server.server_close()
        return not self.thread.is_alive()


def start_server(app: Flask) -> RunningServer:
    server = make_server(LOOPBACK, 0, app, threaded=True)  # port 0 = OS picks a free port
    port = server.server_port
    app.config["ALLOWED_HOSTS"] = {f"{LOOPBACK}:{port}", f"localhost:{port}"}
    thread = threading.Thread(target=server.serve_forever, name="flask-server", daemon=True)
    thread.start()
    return RunningServer(server=server, thread=thread, port=port)
