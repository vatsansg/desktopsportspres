import re

import pytest

from ledsync.config import Config
from ledsync.db import connect, init_db
from ledsync.services import auth
from ledsync.web import create_app


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(data_dir=tmp_path)


@pytest.fixture
def app(cfg):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    auth.seed_admin(conn)
    conn.close()
    application = create_app(cfg)
    application.config["ALLOWED_HOSTS"] = frozenset({"localhost"})  # Flask test client's Host
    return application


@pytest.fixture
def client(app):
    """A client that has NOT gone through the launch handshake."""
    return app.test_client()


@pytest.fixture
def launched(app):
    """A client holding the launch session cookie (what the app's own window has)."""
    c = app.test_client()
    resp = c.get(f"/_launch?t={app.config['LAUNCH_TOKEN']}")
    assert resp.status_code == 302
    return c


def csrf_from(client, path="/login") -> str:
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def do_login(client, username="admin", password="Admin@123", follow=False):
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": csrf_from(client)},
        follow_redirects=follow,
    )


@pytest.fixture
def logged_in(launched):
    assert do_login(launched).status_code == 302
    return launched


def db_rows(cfg, sql, params=()):
    conn = connect(cfg.db_path)
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()
