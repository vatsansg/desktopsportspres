"""Login flow, launch gate, CSRF, throttle, sessions and change-password (routes)."""

import re

import pytest
from conftest import csrf_from, db_rows, do_login

from ledsync.services import auth


# --- launch gate ----------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/login", "/account/password"])
def test_everything_is_forbidden_without_the_launch_cookie(client, path):
    assert client.get(path).status_code == 403


def test_post_is_forbidden_without_the_launch_cookie(client):
    assert client.post("/login", data={"username": "admin", "password": "Admin@123"}).status_code == 403


def test_launch_with_wrong_token_is_forbidden(client):
    assert client.get("/_launch?t=nope").status_code == 403
    assert client.get("/_launch").status_code == 403
    assert client.get("/login").status_code == 403  # still no session


def test_launch_token_is_single_use(app):
    token = app.config["LAUNCH_TOKEN"]
    first = app.test_client().get(f"/_launch?t={token}")
    assert first.status_code == 302
    assert app.test_client().get(f"/_launch?t={token}").status_code == 403


def test_session_cookie_is_httponly_and_samesite_strict(app):
    resp = app.test_client().get(f"/_launch?t={app.config['LAUNCH_TOKEN']}")
    cookie = resp.headers["Set-Cookie"]
    assert cookie.startswith("ledsync_session=")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie


# --- unauthenticated behaviour -------------------------------------------

def test_dashboard_redirects_to_login_when_not_signed_in(launched):
    resp = launched.get("/")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/login")


def test_change_password_page_requires_sign_in(launched):
    assert launched.get("/account/password").status_code == 302


def test_login_page_is_branded_and_has_password_toggle(launched):
    html = launched.get("/login").get_data(as_text=True)
    assert "Sign In" in html and "SIGN IN" in html
    assert 'type="password"' in html
    assert "data-toggle-password" in html
    assert "wtt.css" in html


# --- login ----------------------------------------------------------------

def test_correct_credentials_land_on_the_placeholder_dashboard(launched):
    resp = do_login(launched, follow=True)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Dashboard" in html
    assert "Signed in as" in html and "admin" in html
    assert "Sign Out" in html and "Change Password" in html


def test_wrong_password_shows_an_authentication_error(launched):
    resp = do_login(launched, password="wrong-password")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 401
    assert "Incorrect username or password." in html
    assert 'role="alert"' in html


def test_wrong_username_gives_the_same_generic_error(launched):
    a = do_login(launched, username="nobody")
    b = do_login(launched, password="wrong-password")
    assert a.status_code == b.status_code == 401
    assert "Incorrect username or password." in a.get_data(as_text=True)


def test_failed_login_does_not_echo_what_was_typed(launched):
    html = do_login(launched, username="typedUser123", password="typedPass456").get_data(as_text=True)
    assert "typedUser123" not in html and "typedPass456" not in html


def test_failed_login_does_not_create_a_session(launched):
    do_login(launched, password="wrong")
    assert launched.get("/").status_code == 302


def test_already_signed_in_user_is_redirected_away_from_login(logged_in):
    resp = logged_in.get("/login")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/")


def test_overlong_inputs_do_not_crash(launched):
    resp = do_login(launched, username="a" * 5000, password="b" * 5000)
    assert resp.status_code == 401


def test_oversized_request_body_is_rejected(launched):
    resp = launched.post("/login", data={"username": "a", "password": "b" * 200_000,
                                         "csrf_token": csrf_from(launched)})
    assert resp.status_code == 413


# --- CSRF -----------------------------------------------------------------

def test_login_post_without_csrf_token_is_forbidden(launched):
    assert launched.post("/login", data={"username": "admin", "password": "Admin@123"}).status_code == 403


def test_login_post_with_wrong_csrf_token_is_forbidden(launched):
    launched.get("/login")
    resp = launched.post("/login", data={"username": "admin", "password": "Admin@123",
                                         "csrf_token": "forged"})
    assert resp.status_code == 403


def test_csrf_token_is_accepted_from_header(launched):
    token = csrf_from(launched)
    resp = launched.post("/login", data={"username": "admin", "password": "Admin@123"},
                         headers={"X-CSRF-Token": token})
    assert resp.status_code == 302


def test_logout_and_change_password_require_csrf(logged_in):
    assert logged_in.post("/logout").status_code == 403
    assert logged_in.post("/account/password", data={
        "current_password": "Admin@123", "new_password": "NewSecret#1",
        "confirm_password": "NewSecret#1"}).status_code == 403


def test_csrf_token_rotates_on_login(launched):
    before = csrf_from(launched)
    do_login(launched)
    after = csrf_from(launched, "/")  # dashboard carries the logout form's token
    assert before != after  # session fixation defence


# --- throttle -------------------------------------------------------------

def test_five_failures_block_even_the_correct_password_for_a_while(app, launched):
    clock = {"now": 5000.0}
    app.extensions["ledsync.throttle"] = auth.LoginThrottle(clock=lambda: clock["now"])
    for _ in range(5):
        assert do_login(launched, password="bad").status_code == 401
    blocked = do_login(launched)  # correct credentials
    assert blocked.status_code == 429
    assert "Too many failed attempts" in blocked.get_data(as_text=True)
    assert launched.get("/").status_code == 302  # not signed in

    clock["now"] += 31
    assert do_login(launched).status_code == 302  # released


def test_successful_login_resets_the_failure_count(app, launched):
    app.extensions["ledsync.throttle"] = auth.LoginThrottle(clock=lambda: 1.0)
    for _ in range(4):
        do_login(launched, password="bad")
    assert do_login(launched).status_code == 302
    launched.post("/logout", data={"csrf_token": csrf_from(launched, "/")})
    for _ in range(4):
        assert do_login(launched, password="bad").status_code == 401


# --- logout ---------------------------------------------------------------

def test_logout_ends_the_session_but_keeps_the_window_usable(logged_in):
    resp = logged_in.post("/logout", data={"csrf_token": csrf_from(logged_in, "/")})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/login")
    assert logged_in.get("/").status_code == 302        # signed out
    assert logged_in.get("/login").status_code == 200   # window not locked out


def test_logout_is_post_only(logged_in):
    assert logged_in.get("/logout").status_code == 405


# --- change password ------------------------------------------------------

def _change(client, current="Admin@123", new="NewSecret#1", confirm=None):
    return client.post("/account/password", data={
        "current_password": current, "new_password": new,
        "confirm_password": new if confirm is None else confirm,
        "csrf_token": csrf_from(client, "/account/password")})


def test_change_password_success_flow(logged_in):
    resp = _change(logged_in)
    assert resp.status_code == 302
    assert "Password changed." in logged_in.get("/").get_data(as_text=True)

    logged_in.post("/logout", data={"csrf_token": csrf_from(logged_in, "/")})
    assert do_login(logged_in, password="Admin@123").status_code == 401  # old password dead
    assert do_login(logged_in, password="NewSecret#1").status_code == 302


def test_change_password_persists_across_restart(cfg, logged_in):
    _change(logged_in)
    from ledsync.db import connect
    conn = connect(cfg.db_path)
    auth.seed_admin(conn)  # what every app start does
    assert auth.check_credentials(conn, "admin", "NewSecret#1")
    conn.close()


@pytest.mark.parametrize("kwargs,fragment", [
    (dict(current="wrong"), "Current password is incorrect"),
    (dict(confirm="mismatch"), "do not match"),
    (dict(new="short"), "at least 8"),
    (dict(new="Admin@123"), "must be different"),
])
def test_change_password_errors_are_shown_and_nothing_changes(logged_in, kwargs, fragment):
    resp = _change(logged_in, **kwargs)
    assert resp.status_code == 400
    assert fragment in resp.get_data(as_text=True)
    logged_in.post("/logout", data={"csrf_token": csrf_from(logged_in, "/")})
    assert do_login(logged_in).status_code == 302  # original password still works


def test_change_password_wrong_current_is_throttled(app, logged_in):
    app.extensions["ledsync.throttle"] = auth.LoginThrottle(clock=lambda: 1.0)
    for _ in range(5):
        assert _change(logged_in, current="wrong").status_code == 400
    blocked = _change(logged_in, current="Admin@123")
    assert blocked.status_code == 429


# --- operational log ------------------------------------------------------

def test_login_events_are_logged_without_any_credentials(cfg, launched):
    do_login(launched, username="typedUser123", password="typedPass456")
    do_login(launched)
    launched.post("/logout", data={"csrf_token": csrf_from(launched, "/")})
    rows = db_rows(cfg, "SELECT operation, status, message, timestamp FROM operation_log ORDER BY log_id")
    assert [(r["operation"], r["status"]) for r in rows] == [
        ("Login", "Failed"), ("Login", "Success"), ("Logout", "Success")]
    blob = " ".join(f"{r['message']} {r['operation']}" for r in rows)
    for secret in ("typedUser123", "typedPass456", "Admin@123"):
        assert secret not in blob
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", r["timestamp"]) for r in rows)


def test_password_change_is_logged_without_the_passwords(cfg, logged_in):
    _change(logged_in, new="NewSecret#1")
    _change(logged_in, current="bad", new="AnotherOne#9")
    rows = db_rows(cfg, "SELECT operation, status, message FROM operation_log "
                        "WHERE operation = 'Password Change' ORDER BY log_id")
    assert [r["status"] for r in rows] == ["Success", "Failed"]
    blob = " ".join(r["message"] for r in rows)
    for secret in ("NewSecret#1", "AnotherOne#9", "Admin@123"):
        assert secret not in blob


def test_blocked_attempts_are_logged(app, cfg, launched):
    app.extensions["ledsync.throttle"] = auth.LoginThrottle(clock=lambda: 1.0)
    for _ in range(6):
        do_login(launched, password="bad")
    statuses = [r["status"] for r in db_rows(cfg, "SELECT status FROM operation_log ORDER BY log_id")]
    assert statuses == ["Failed"] * 5 + ["Blocked"]


# --- review fixes (Phase 1 architect review) ------------------------------

def _cookie_value(client):
    return client.get_cookie("ledsync_session").value


def _replay_client(app, cookie_value):
    other = app.test_client()
    other.set_cookie("ledsync_session", cookie_value)
    return other


def test_old_session_cookie_is_revoked_after_logout(app, logged_in):
    """Sessions are signed cookies; a replayed pre-logout cookie must stop working."""
    stolen = _cookie_value(logged_in)
    assert _replay_client(app, stolen).get("/").status_code == 200   # valid before logout
    logged_in.post("/logout", data={"csrf_token": csrf_from(logged_in, "/")})
    replay = _replay_client(app, stolen).get("/")
    assert replay.status_code == 302 and replay.headers["Location"].endswith("/login")


def test_password_change_revokes_other_sessions_but_keeps_this_one(app, logged_in):
    stolen = _cookie_value(logged_in)
    assert _replay_client(app, stolen).get("/").status_code == 200
    assert _change(logged_in).status_code == 302
    assert _replay_client(app, stolen).get("/").status_code == 302   # old cookie dead
    assert logged_in.get("/").status_code == 200                     # operator stays signed in


def test_change_password_typos_never_accumulate_toward_a_lockout(logged_in):
    for _ in range(12):
        assert _change(logged_in, confirm="mismatch").status_code == 400
    assert _change(logged_in).status_code == 302   # never hit 429


def test_lost_cookie_shows_a_branded_page_not_a_bare_403(client):
    resp = client.get("/login")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 403
    assert "Session Not Valid" in html and "start it again" in html
    assert "Werkzeug" not in html and "Forbidden" not in html
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_error_page_styles_load_without_a_session(client):
    """The branded 403 needs its stylesheet, so static assets are exempt from the gate."""
    assert client.get("/static/css/wtt.css").status_code == 200
    assert client.get("/static/js/app.js").status_code == 200


def test_static_exemption_does_not_expose_anything_else(client):
    for path in ("/", "/login", "/account/password", "/_launch", "/static/../ledsync.db"):
        assert client.get(path).status_code in (403, 404)


def test_oversized_request_shows_a_branded_413(launched):
    resp = launched.post("/login", data={"password": "x" * 200_000, "csrf_token": csrf_from(launched)})
    assert resp.status_code == 413 and "Request Too Large" in resp.get_data(as_text=True)


def test_login_error_is_linked_to_the_inputs_for_screen_readers(launched):
    html = do_login(launched, password="bad").get_data(as_text=True)
    assert 'id="form-error"' in html
    assert html.count('aria-describedby="form-error"') == 2  # username + password
    assert "aria-pressed" not in html


def test_new_security_headers_present(launched):
    h = launched.get("/login").headers
    assert "object-src 'none'" in h["Content-Security-Policy"]
    assert h["Cross-Origin-Opener-Policy"] == "same-origin"
    assert h["Cross-Origin-Resource-Policy"] == "same-origin"
    assert "camera=()" in h["Permissions-Policy"]
