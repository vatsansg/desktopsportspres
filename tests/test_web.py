"""Step 0.1 - the Flask shell and its loopback-only server lifecycle."""

import socket
import urllib.error
import urllib.request

import pytest

from ledsync.web import create_app, start_server


@pytest.fixture
def client(cfg):
    app = create_app(cfg)
    app.config["ALLOWED_HOSTS"] = frozenset({"localhost"})  # Flask test client's default Host
    return app.test_client()


def test_host_check_fails_closed_by_default(cfg):
    """A freshly created app refuses every request until hosts are configured."""
    assert create_app(cfg).test_client().get("/").status_code == 400


def test_localhost_host_header_accepted_by_real_server(cfg):
    running = start_server(create_app(cfg))
    try:
        req = urllib.request.Request(running.url, headers={"Host": f"localhost:{running.port}"})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
    finally:
        running.stop()


def test_security_headers_also_on_error_responses(client):
    h = client.get("/does-not-exist").headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"]


def test_csp_blocks_base_uri_and_foreign_forms(client):
    csp = client.get("/").headers["Content-Security-Policy"]
    assert "base-uri 'none'" in csp
    assert "form-action 'self'" in csp


def test_secret_key_is_random_per_app(cfg):
    a, b = create_app(cfg), create_app(cfg)
    assert a.config["SECRET_KEY"] != b.config["SECRET_KEY"]
    assert len(a.config["SECRET_KEY"]) >= 32


def test_get_db_is_per_request_and_closed_after(cfg):
    import sqlite3

    from ledsync.db import init_db
    from ledsync.web.app import get_db

    init_db(cfg.db_path)
    app = create_app(cfg)
    with app.app_context():
        db = get_db()
        assert get_db() is db  # same connection within one request
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    with pytest.raises(sqlite3.ProgrammingError):  # closed by teardown
        db.execute("SELECT 1")


def test_index_renders_branded_shell(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "LED Asset Download &amp; Sync" in body
    assert "css/wtt.css" in body


def test_wtt_tokens_are_defined_in_css(client):
    css = client.get("/static/css/wtt.css").get_data(as_text=True)
    for token, hex_ in [("--wtt-black", "#000000"), ("--wtt-white", "#FFFFFF"),
                        ("--wtt-orange", "#FF6B00"), ("--wtt-blue", "#007AD9"),
                        ("--wtt-teal", "#2BABD2")]:
        assert f"{token}: {hex_}" in css


def test_security_headers_present(client):
    h = client.get("/").headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in h["Content-Security-Policy"]
    assert h["Cache-Control"] == "no-store"


def test_unknown_route_is_404(client):
    assert client.get("/does-not-exist").status_code == 404


def test_server_binds_loopback_only_and_serves(cfg):
    running = start_server(create_app(cfg))
    try:
        assert running.server.server_address[0] == "127.0.0.1"
        assert running.port > 0
        with urllib.request.urlopen(running.url, timeout=5) as r:
            assert r.status == 200
    finally:
        assert running.stop()


def test_foreign_host_header_rejected(cfg):
    """DNS-rebinding defence: a request carrying an unexpected Host is refused."""
    running = start_server(create_app(cfg))
    try:
        req = urllib.request.Request(running.url, headers={"Host": "evil.example.com"})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code == 400
    finally:
        running.stop()


def test_stop_releases_port_and_thread(cfg):
    running = start_server(create_app(cfg))
    port = running.port
    assert running.stop() is True
    assert not running.thread.is_alive()
    # Port is free again: we can bind it ourselves.
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))


def test_two_instances_get_different_ports(cfg):
    a = start_server(create_app(cfg))
    b = start_server(create_app(cfg))
    try:
        assert a.port != b.port
    finally:
        a.stop()
        b.stop()
