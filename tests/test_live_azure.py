"""LIVE checks against the real Azure Storage account (Step 4.2 validation).

Opt-in only - the normal test run never touches the network:

    .\\.venv\\Scripts\\python -m pytest -q --live tests/test_live_azure.py

Needs STORAGE_ACCOUNT_NAME / STORAGE_ACCOUNT_KEY in the git-ignored .env (see
scripts\\set_storage_key.ps1). Every check here is READ-ONLY: it lists containers and folders and
downloads one small file. Nothing is ever written to Azure, and the key is never printed.
"""

import json
import os
import re
import uuid

import pytest
from conftest import csrf_from, db_rows

from ledsync import config as app_config
from ledsync.services import cloud, exceptions
from ledsync.services import registration as reg
from ledsync.services import settings as cs
from ledsync.services.storage import AzureReadOnlyStorage, StorageError
from ledsync.web import create_app

# Captured at import, before the hermetic fixture hides the developer environment from tests.
REAL_DOTENV = app_config.read_dotenv(app_config.PROJECT_ROOT / ".env")


def _real(name: str) -> str:
    return os.environ.get(name) or REAL_DOTENV.get(name, "")


ACCOUNT = _real("STORAGE_ACCOUNT_NAME") or "sasportspresentation"
KEY = _real("STORAGE_ACCOUNT_KEY")
CONTAINER = _real("STORAGE_CONTAINER") or "2026"

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not KEY, reason="no STORAGE_ACCOUNT_KEY in .env / environment"),
]

EXPECTED_GUID = "eb0153b9-b74a-45e8-9f0f-b2c1a1b7b459"    # the real test event exported on 18 Sep 2026


@pytest.fixture(scope="module")
def storage():
    return AzureReadOnlyStorage(ACCOUNT, KEY)


def test_connects_and_lists_the_year_container(storage):
    report = storage.test_connection(CONTAINER)
    assert report.account == ACCOUNT and CONTAINER in report.year_containers
    assert report.preferred_container_found is True


def test_finds_the_real_test_event_folder_from_just_its_id(storage):
    loc = storage.find_event("1000", CONTAINER)
    assert loc.container == CONTAINER and loc.folder == "1000 - Star contender Doha"


def test_downloads_and_fully_validates_the_real_guid_json(storage):
    loc = storage.find_event("1000", CONTAINER)
    data = storage.read_blob(loc, "_GUID.json", reg.MAX_CONFIG_BYTES)
    config = reg.parse_event_config(data)
    cloud.verify_storage_location(config, ACCOUNT, loc)              # S-14 against the real address
    assert (config.event_id, config.event_name) == ("1000", "Star contender Doha")
    assert config.guid == EXPECTED_GUID
    assert [(t.number, t.inner, t.outer, t.main) for t in config.tables] == [(1, True, True, True), (2, True, False, False)]
    assert config.storage_url.startswith(f"https://{ACCOUNT}.blob.core.windows.net/{CONTAINER}/")


def test_an_event_that_does_not_exist_is_a_clear_missing_folder_error(storage):
    with pytest.raises(StorageError) as exc:
        storage.find_event("987654321", CONTAINER)
    assert exc.value.category == exceptions.MISSING_FOLDER


def test_a_wrong_key_is_refused_by_azure_with_a_permission_error():
    import base64

    bogus = base64.b64encode(uuid.uuid4().bytes * 4).decode()          # well-formed, but not the real key
    with pytest.raises(StorageError) as exc:
        AzureReadOnlyStorage(ACCOUNT, bogus).test_connection(CONTAINER)
    assert exc.value.category == exceptions.PERMISSION
    assert bogus not in exc.value.message and KEY not in exc.value.message


def test_a_wrong_account_name_is_a_connectivity_error_not_a_crash():
    with pytest.raises(StorageError) as exc:
        AzureReadOnlyStorage("zz" + uuid.uuid4().hex[:18], KEY).test_connection("")
    assert exc.value.category in (exceptions.STORAGE_CONNECTIVITY, exceptions.PERMISSION)


def test_step_4_2_registers_the_real_event_end_to_end_through_the_app(cfg, monkeypatch):
    """The real application code, real Azure (read-only), a throw-away local database."""
    from ledsync.db import connect, init_db
    from ledsync.services import auth

    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    auth.seed_admin(conn)
    cs.save_cloud(conn, ACCOUNT, CONTAINER, KEY)
    conn.close()

    app = create_app(cfg)
    app.config["ALLOWED_HOSTS"] = frozenset({"localhost"})
    client = app.test_client()
    client.get(f"/_launch?t={app.config['LAUNCH_TOKEN']}")
    client.post("/login", data={"username": "admin", "password": auth.DEFAULT_PASSWORD,
                                "csrf_token": csrf_from(client, "/login")})
    resp = client.post("/events/new", data={"event_id": "1000", "csrf_token": csrf_from(client, "/events/new")})
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/")

    [row] = db_rows(cfg, "SELECT * FROM events")
    assert (row["event_id"], row["event_name"], row["event_guid"], row["status"]) == (
        "1000", "Star contender Doha", EXPECTED_GUID, "Registered")
    assert row["configuration_file"].startswith(f"https://{ACCOUNT}.blob.core.windows.net/{CONTAINER}/")
    assert row["configuration_file"].endswith("/_GUID.json") and KEY not in row["configuration_file"]
    stored = json.loads(row["configuration_json"])
    assert stored["exportGuid"] == EXPECTED_GUID and len(stored["tables"]) == 2
    assert re.match(r"^2026-09-18T07:02:26", row["last_updated"])
    # nothing sensitive was written anywhere in the local database
    everything = json.dumps({t: db_rows(cfg, f"SELECT * FROM {t}")
                             for t in ("events", "operation_log", "exception_log")})
    assert KEY not in everything

    # a second registration of the same event is a harmless no-op
    again = client.post("/events/new", data={"event_id": "1000", "csrf_token": csrf_from(client, "/events/new")})
    assert again.status_code == 302 and len(db_rows(cfg, "SELECT * FROM events")) == 1


def test_phase6_downloads_parses_and_compares_the_real_change_log(cfg):
    """Step 6.1 / 6.3 against the REAL event: the whole check, read-only, through the real page."""
    from ledsync.db import connect, init_db
    from ledsync.services import auth
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    auth.seed_admin(conn)
    cs.save_cloud(conn, ACCOUNT, CONTAINER, KEY)
    conn.close()
    app = create_app(cfg)
    app.config["ALLOWED_HOSTS"] = frozenset({"localhost"})
    client = app.test_client()
    client.get(f"/_launch?t={app.config['LAUNCH_TOKEN']}")
    client.post("/login", data={"username": "admin", "password": auth.DEFAULT_PASSWORD,
                                "csrf_token": csrf_from(client, "/login")})
    client.post("/events/new", data={"event_id": "1000", "csrf_token": csrf_from(client, "/events/new")})

    resp = client.post("/events/1000/changes/check",
                       data={"csrf_token": csrf_from(client, "/events/1000/changes")}, follow_redirects=True)
    html = resp.get_data(as_text=True)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    assert resp.status_code == 200 and "Nothing was downloaded or changed" in html and KEY not in html
    assert "file _ledassetschangelog.csv" in text
    # The real log holds Table 1 Inner AND Outer sponsorsequence.csv as distinct files (web BRD v2.4).
    assert "Table 1 Inner sponsorsequence.csv" in text and "Table 1 Outer sponsorsequence.csv" in text
    assert "RPI HOME_Look.png" in text                               # the RPI file is a real destination now
    assert "Table 2 Inner sponsorsequence.csv" in text                # the sponsor sequence uploaded to Table 2
    # nothing local was created or changed by a check
    assert db_rows(cfg, "SELECT COUNT(*) AS n FROM download_history")[0]["n"] == 0
    assert (cfg.data_dir / "_localchangelog.csv").read_text(encoding="utf-8-sig").count("\n") == 1   # headers only


def test_phase6_downloads_the_real_rpi_file_into_the_rpi_folder(cfg):
    """The real `RPI/HOME_Look.png` (Azure holds it as `rpi/HOME_Look.png`) is downloaded, read-only, into the
    default RPI folder of a scratch data folder."""
    from ledsync.db import connect, init_db
    from ledsync.services import auth
    init_db(cfg.db_path)
    conn = connect(cfg.db_path)
    auth.seed_admin(conn)
    cs.save_cloud(conn, ACCOUNT, CONTAINER, KEY)
    conn.close()
    app = create_app(cfg)
    app.config["ALLOWED_HOSTS"] = frozenset({"localhost"})
    client = app.test_client()
    client.get(f"/_launch?t={app.config['LAUNCH_TOKEN']}")
    client.post("/login", data={"username": "admin", "password": auth.DEFAULT_PASSWORD,
                                "csrf_token": csrf_from(client, "/login")})
    client.post("/events/new", data={"event_id": "1000", "csrf_token": csrf_from(client, "/events/new")})
    client.post("/events/1000/changes/check", data={"csrf_token": csrf_from(client, "/events/1000/changes")})
    resp = client.post("/events/1000/changes/rpi", data={"csrf_token": csrf_from(client, "/events/1000/changes")},
                       follow_redirects=True)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200 and KEY not in html and "RPI files: 1 downloaded, 0 removed, 0 failed." in html
    saved = cfg.data_dir / "RPI" / "HOME_Look.png"
    assert saved.is_file() and saved.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and saved.stat().st_size > 100
    assert sorted(p.name for p in (cfg.data_dir / "RPI").iterdir()) == ["HOME_Look.png"]
    [row] = db_rows(cfg, "SELECT * FROM download_history")
    assert (row["led_type"], row["file_name"], row["status"], row["table_number"]) == ("RPI", "HOME_Look.png", "Success", None)
    assert KEY not in json.dumps(row) and "No RPI files are waiting." in client.get("/events/1000/changes").get_data(as_text=True)
    print(f"downloaded {saved.stat().st_size} bytes")
