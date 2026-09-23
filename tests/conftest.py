import json
import re
from pathlib import Path
from urllib.parse import quote

import pytest
from fakes import ACCOUNT, FAKE_KEY, FakeBlobService

from ledsync import config as app_config
from ledsync.config import Config
from ledsync.db import connect, init_db
from ledsync.services import auth
from ledsync.services.storage import AzureReadOnlyStorage
from ledsync.web import create_app

def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False,
                     help="run the opt-in tests that talk to the REAL Azure Storage account (read-only)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="live Azure test - run with --live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


TESTFILES = Path(__file__).resolve().parent.parent / "docs" / "testfiles" / "phase3"


@pytest.fixture(autouse=True)
def hermetic_environment(monkeypatch, tmp_path_factory):
    """No test may see a developer's real .env or Storage variables."""
    monkeypatch.setattr(app_config, "DOTENV_PATH", tmp_path_factory.mktemp("noenv") / "no.env")
    for name in ("STORAGE_ACCOUNT_NAME", "STORAGE_ACCOUNT_KEY", "STORAGE_CONTAINER"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def no_real_schtasks(monkeypatch):
    """No test may shell out to the real Windows Task Scheduler. A safety-net default (always
    "not registered", never actually invoked) for any test that incidentally touches Settings ->
    Scheduling without caring about scheduler.py itself; tests/test_phase12_scheduled.py passes its
    own explicit fake runner wherever the real behaviour matters."""
    from ledsync.services import scheduler

    class _NeverCalled:
        returncode = 1
        stdout = stderr = ""

    monkeypatch.setattr(scheduler, "_DEFAULT_RUNNER", lambda *a, **kw: _NeverCalled())


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(data_dir=tmp_path)


@pytest.fixture
def azure(monkeypatch) -> FakeBlobService:
    """The fake Azure blob service the application talks to in tests, with the cloud settings
    supplied through the development-environment fallback (so no settings rows are written)."""
    monkeypatch.setenv("STORAGE_ACCOUNT_NAME", ACCOUNT)
    monkeypatch.setenv("STORAGE_ACCOUNT_KEY", FAKE_KEY)
    monkeypatch.setenv("STORAGE_CONTAINER", "2026")
    fake = FakeBlobService()
    fake.containers.add("2026")
    return fake


@pytest.fixture
def app(cfg, azure):
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    auth.seed_admin(conn)
    conn.close()
    application = create_app(cfg)
    application.config["ALLOWED_HOSTS"] = frozenset({"localhost"})  # Flask test client's Host
    application.config["SYNC_JOBS"] = True                          # background downloads run inline, so tests are deterministic
    application.extensions["ledsync.storage_factory"] = lambda settings: AzureReadOnlyStorage(
        settings.account, settings.access_key, service_factory=azure.factory)
    application.extensions["test.azure"] = azure
    return application


def event_folder(event_id: str, name: str) -> str:
    return f"{event_id} - {name}"


def event_url(container: str, folder: str, account: str = ACCOUNT) -> str:
    return f"https://{account}.blob.core.windows.net/{container}/{quote(folder)}"


def serve_event(azure: FakeBlobService, file_or_obj, *, container="2026", folder=None, fix_url=True,
                raw: bytes | None = None) -> str:
    """Put an event's _GUID.json where the web application would (container/'<id> - <name>'/_GUID.json).
    `file_or_obj` is a test-file name or a dict. The file's storage address is aligned to the folder
    unless fix_url=False. Returns the folder used."""
    if raw is not None:
        azure.put(container, f"{folder}/_GUID.json", raw)
        return folder
    obj = (json.loads((TESTFILES / file_or_obj).read_text(encoding="utf-8"))
           if isinstance(file_or_obj, str) else dict(file_or_obj))
    folder = folder or event_folder(obj.get("eventId", "0"), obj.get("eventName", "x"))
    if fix_url:
        obj["eventStorageUrl"] = event_url(container, folder)
    azure.put(container, f"{folder}/_GUID.json", json.dumps(obj))
    return folder


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
