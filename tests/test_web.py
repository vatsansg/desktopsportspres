"""Web shell: brand tokens, security headers, and the loopback server lifecycle."""

import http.cookiejar
import socket
import urllib.error
import urllib.request

import pytest

from ledsync.web import create_app, start_server
from ledsync.web.app import get_db


def test_wtt_tokens_are_defined_in_css(launched):
    css = launched.get("/static/css/wtt.css").get_data(as_text=True)
    for token, hex_ in [("--wtt-black", "#000000"), ("--wtt-white", "#FFFFFF"),
                        ("--wtt-orange", "#FF6B00"), ("--wtt-blue", "#007AD9"),
                        ("--wtt-teal", "#2BABD2")]:
        assert f"{token}: {hex_}" in css


def test_orange_is_used_only_for_the_primary_button(launched):
    """Brand rule: orange is reserved for the single primary action - never for
    text, borders or decoration."""
    css = launched.get("/static/css/wtt.css").get_data(as_text=True)
    uses = [line.strip() for line in css.splitlines()
            if "var(--wtt-orange" in line and not line.strip().startswith("--")]
    assert uses and all(line.startswith(".btn-primary") for line in uses), uses


def test_security_headers_present(launched):
    h = launched.get("/login").headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in h["Content-Security-Policy"]
    assert h["Cache-Control"] == "no-store"


def test_security_headers_also_on_error_responses(launched):
    resp = launched.get("/does-not-exist")
    assert resp.status_code == 404
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]


def test_csp_blocks_base_uri_and_foreign_forms(launched):
    csp = launched.get("/login").headers["Content-Security-Policy"]
    assert "base-uri 'none'" in csp
    assert "form-action 'self'" in csp


def test_pages_use_no_inline_script_or_style(launched):
    """CSP is script-src/style-src 'self'; inline code would silently break."""
    for path in ("/login",):
        html = launched.get(path).get_data(as_text=True)
        assert "<script>" not in html and " style=" not in html and "<style" not in html
        assert " onclick=" not in html


def test_host_check_fails_closed_by_default(cfg):
    assert create_app(cfg).test_client().get("/login").status_code == 400


def test_secret_key_is_random_per_app(cfg):
    a, b = create_app(cfg), create_app(cfg)
    assert a.config["SECRET_KEY"] != b.config["SECRET_KEY"]
    assert len(a.config["SECRET_KEY"]) >= 32
    assert a.config["LAUNCH_TOKEN"] != b.config["LAUNCH_TOKEN"]


def test_get_db_is_per_request_and_closed_after(app):
    import sqlite3

    with app.app_context():
        db = get_db()
        assert get_db() is db  # same connection within one request
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    with pytest.raises(sqlite3.ProgrammingError):  # closed by teardown
        db.execute("SELECT 1")


# --- real server lifecycle ------------------------------------------------

def test_server_binds_loopback_only_and_serves_after_launch(app):
    running = start_server(app)
    try:
        assert running.server.server_address[0] == "127.0.0.1"
        assert running.port > 0
        # Behave like the browser: keep cookies across the launch -> / -> /login redirects.
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with opener.open(running.launch_url, timeout=5) as r:
            assert r.status == 200
            assert "Sign In" in r.read().decode()
    finally:
        assert running.stop()


def test_real_server_refuses_requests_without_the_launch_cookie(app):
    running = start_server(app)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(running.url + "login", timeout=5)
        assert exc.value.code == 403
    finally:
        running.stop()


def test_foreign_host_header_rejected(app):
    """DNS-rebinding defence: a request carrying an unexpected Host is refused."""
    running = start_server(app)
    try:
        req = urllib.request.Request(running.launch_url, headers={"Host": "evil.example.com"})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code == 400
    finally:
        running.stop()


@pytest.mark.parametrize("host", [
    "localhost:{port}",        # can resolve to ::1 - deliberately not allowed
    "127.0.0.1",               # no port
    "127.0.0.1.:{port}",       # trailing dot
    "[::1]:{port}",
    "127.0.0.1:1",             # wrong port
])
def test_only_the_exact_loopback_host_is_accepted(app, host):
    running = start_server(app)
    try:
        req = urllib.request.Request(running.launch_url,
                                     headers={"Host": host.format(port=running.port)})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code == 400
    finally:
        running.stop()


def test_exact_loopback_host_is_accepted(app):
    running = start_server(app)
    try:
        req = urllib.request.Request(running.launch_url, headers={"Host": f"127.0.0.1:{running.port}"})
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with opener.open(req, timeout=5) as r:
            assert r.status == 200
    finally:
        running.stop()


def test_server_does_not_advertise_its_software_versions(app):
    running = start_server(app)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:  # 403 without cookie is fine here
            urllib.request.urlopen(running.url + "login", timeout=5)
        server = exc.value.headers["Server"]
        assert server == "ledsync" and "Werkzeug" not in server and "Python" not in server
    finally:
        running.stop()


def test_launch_token_is_not_in_the_object_repr(app):
    running = start_server(app)
    try:
        assert running.launch_token not in repr(running)
    finally:
        running.stop()


def test_stop_releases_port_and_thread(app):
    running = start_server(app)
    port = running.port
    assert running.stop() is True
    assert not running.thread.is_alive()
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
