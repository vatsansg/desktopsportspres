"""Phase 2 - Event list: display formatting, ordering, statuses and the dashboard."""

import os
import subprocess
import sys
from datetime import timedelta, timezone
from pathlib import Path

import pytest
from conftest import do_login

from ledsync.db import connect, init_db
from ledsync.services import events

PLUS3 = timezone(timedelta(hours=3))   # e.g. Doha
ROOT = Path(__file__).resolve().parent.parent


# --- timestamp formatting (BRD 7.2: DD/MM/YY HH:MM, 24-hour) ---------------

@pytest.mark.parametrize("stored,expected", [
    ("2026-09-08T16:45:00Z", "08/09/26 19:45"),          # BRD's own example, UTC+3
    ("2026-09-18T07:02:26.380Z", "18/09/26 10:02"),      # real export timestamp, fractional secs
    ("2026-09-08 16:45:00", "08/09/26 19:45"),           # space separator, no zone -> UTC
    ("2026-09-08T19:45:00+03:00", "08/09/26 19:45"),     # explicit offset
    ("2026-09-08T19:45:00+05:30", "08/09/26 17:15"),
    ("2026-01-05T22:30:00Z", "06/01/26 01:30"),          # crosses midnight -> next day
    ("2026-09-08T00:00:00Z", "08/09/26 03:00"),
    ("2026-09-08T09:05:00Z", "08/09/26 12:05"),          # zero-padded 24h, never AM/PM
])
def test_display_format_is_dd_mm_yy_hh_mm_in_the_chosen_zone(stored, expected):
    assert events.format_timestamp(stored, PLUS3) == expected


def test_local_time_display_is_correct_across_the_whole_supported_range():
    """tz=None (this machine's local time) must work for EVERY accepted instant and agree
    with an independent computation of the machine's UTC offset at that instant."""
    import re
    from datetime import datetime

    for stored in ["1970-01-01T00:00:00Z", "1970-01-01T12:00:00Z", "1999-12-31T23:59:59Z",
                   "2026-01-15T07:02:26Z", "2026-07-15T07:02:26Z",   # winter + summer
                   "2038-01-19T03:14:07Z", "2098-12-31T23:59:59Z"]:
        out = events.format_timestamp(stored)
        assert re.fullmatch(r"\d\d/\d\d/\d\d \d\d:\d\d", out), (stored, out)
        local = datetime.fromisoformat(stored.replace("Z", "+00:00")).astimezone()
        assert out == f"{local.day:02d}/{local.month:02d}/{local.year % 100:02d} {local.hour:02d}:{local.minute:02d}"


def test_display_is_24_hour_with_no_am_pm():
    out = events.format_timestamp("2026-09-08T13:07:00Z", timezone.utc)
    assert out == "08/09/26 13:07" and "PM" not in out.upper()


@pytest.mark.parametrize("stored", [None, "", "   "])
def test_empty_timestamp_shows_an_em_dash(stored):
    assert events.format_timestamp(stored, PLUS3) == events.EMPTY


def test_unparseable_timestamp_is_shown_raw_not_hidden_and_never_crashes():
    assert events.format_timestamp("yesterday-ish", PLUS3) == "yesterday-ish"


# --- status vocabulary (owner decision: Registered / Ready / Synced / Attention needed) ---

@pytest.mark.parametrize("stored,text,key", [
    (None, "Registered", "registered"),
    ("", "Registered", "registered"),
    ("  ", "Registered", "registered"),
    ("Registered", "Registered", "registered"),
    ("ready", "Ready", "ready"),
    ("SYNCED", "Synced", "synced"),
    ("attention NEEDED", "Attention needed", "attention"),
    ("Some future status", "Some future status", "other"),   # never dropped
])
def test_status_normalisation(stored, text, key):
    assert events.normalise_status(stored) == (text, key)


# --- list_events ----------------------------------------------------------

@pytest.fixture
def conn(cfg):
    init_db(cfg.db_path)
    c = connect(cfg.db_path)
    yield c
    c.close()


def _add(conn, event_id, name, updated=None, status=None):
    conn.execute("INSERT INTO events (event_id, event_name, last_updated, status) VALUES (?,?,?,?)",
                 (event_id, name, updated, status))
    conn.commit()


def test_empty_register_returns_empty_list(conn):
    assert events.list_events(conn, PLUS3) == []


def test_one_row_renders_all_four_fields(conn):
    _add(conn, "1000", "Star contender Doha", "2026-09-18T07:02:26Z", "Ready")
    [row] = events.list_events(conn, PLUS3)
    assert (row.event_id, row.event_name, row.last_updated, row.status) == (
        "1000", "Star contender Doha", "18/09/26 10:02", "Ready")


def test_most_recently_updated_first_undated_last_then_by_id(conn):
    _add(conn, "3", "Old", "2026-01-01T00:00:00Z")
    _add(conn, "1", "Newest", "2026-09-20T00:00:00Z")
    _add(conn, "9", "Undated B", None)
    _add(conn, "2", "Middle", "2026-06-01T00:00:00Z")
    _add(conn, "5", "Undated A", "")
    _add(conn, "4", "Garbage date", "not a date")
    order = [r.event_id for r in events.list_events(conn, PLUS3)]
    assert order == ["1", "2", "3", "4", "5", "9"]


def test_mixed_timestamp_formats_sort_by_real_time_not_text(conn):
    _add(conn, "A", "a", "2026-09-08T23:00:00-05:00")   # = 09 Sep 04:00 UTC  (latest)
    _add(conn, "B", "b", "2026-09-08T22:00:00Z")
    _add(conn, "C", "c", "2026-09-08 21:00:00")
    assert [r.event_id for r in events.list_events(conn, timezone.utc)] == ["A", "B", "C"]


def test_numeric_event_id_from_hand_inserted_row_is_shown_as_text(conn):
    conn.execute("INSERT INTO events (event_id, event_name) VALUES (1000, 'X')")
    conn.commit()
    assert events.list_events(conn)[0].event_id == "1000"


# --- dashboard page -------------------------------------------------------

def _dashboard(app, launched):
    do_login(launched)
    return launched.get("/")


def test_dashboard_with_no_events_shows_a_clean_empty_state(app, launched):
    resp = _dashboard(app, launched)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "No Events Registered Yet" in html
    assert "<table" not in html and "Traceback" not in html and "None" not in html


def test_dashboard_renders_a_manually_inserted_row(app, cfg, launched):
    """Step 2.1 validation: insert a test row, confirm it renders incl. the timestamp format."""
    app.config["DISPLAY_TZ"] = PLUS3
    conn = connect(cfg.db_path)
    _add(conn, "1000", "Star contender Doha", "2026-09-08T16:45:00Z", "")
    conn.close()
    html = _dashboard(app, launched).get_data(as_text=True)
    assert "<table" in html
    for header in ("Event ID", "Event Name", "Last Updated", "Status"):
        assert f">{header}</th>" in html
    assert ">1000</a>" in html and "/events/1000" in html
    assert "Star contender Doha" in html
    assert "08/09/26 19:45" in html                      # BRD 7.2's own example format
    assert "badge-registered" in html and ">Registered<" in html
    assert "No Events Registered Yet" not in html
    assert "1 registered" in html


def test_dashboard_shows_every_status_badge(app, cfg, launched):
    conn = connect(cfg.db_path)
    for i, st in enumerate(["", "Ready", "Synced", "Attention needed", "Odd"]):
        _add(conn, str(100 + i), f"E{i}", "2026-09-08T10:00:00Z", st)
    conn.close()
    html = _dashboard(app, launched).get_data(as_text=True)
    for cls in ("registered", "ready", "synced", "attention", "other"):
        assert f"badge-{cls}" in html


def test_dashboard_escapes_event_names_no_html_injection(app, cfg, launched):
    conn = connect(cfg.db_path)
    _add(conn, "1", '<script>alert(1)</script><img src=x onerror=alert(2)>', "2026-09-08T10:00:00Z")
    conn.close()
    html = _dashboard(app, launched).get_data(as_text=True)
    assert "<script>alert(1)" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html


def test_dashboard_table_is_accessible(app, cfg, launched):
    conn = connect(cfg.db_path)
    _add(conn, "1", "E", "2026-09-08T10:00:00Z")
    conn.close()
    html = _dashboard(app, launched).get_data(as_text=True)
    assert html.count('scope="col"') == 4 and "<caption" in html
    assert "local time" in html


def test_dashboard_is_still_login_gated(launched, client):
    assert launched.get("/").status_code == 302     # not signed in
    assert client.get("/").status_code == 403       # no launch cookie


def test_a_bad_row_never_breaks_the_page(app, cfg, launched):
    conn = connect(cfg.db_path)
    _add(conn, "1", "", "garbage", "???")
    conn.close()
    resp = _dashboard(app, launched)
    assert resp.status_code == 200 and "garbage" in resp.get_data(as_text=True)


# --- dev helper script ----------------------------------------------------

def _run_script(args, data_dir=None):
    env = {**os.environ}
    env.pop("LEDSYNC_DATA_DIR", None)
    if data_dir:
        env["LEDSYNC_DATA_DIR"] = str(data_dir)
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "insert_test_event.py"), *args],
                          capture_output=True, text=True, env=env, timeout=60)


def test_script_refuses_to_touch_default_data_dir():
    r = _run_script([])
    assert r.returncode == 2 and "Refusing" in r.stderr


def test_script_inserts_one_row_then_clear_removes_only_its_rows(tmp_path):
    assert _run_script([], tmp_path).returncode == 0
    from ledsync.config import Config

    db = Config(data_dir=tmp_path).db_path
    conn = connect(db)
    conn.execute("INSERT INTO events (event_id, event_name) VALUES ('REAL', 'Keep me')")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
    assert _run_script(["--clear"], tmp_path).returncode == 0
    remaining = [r["event_id"] for r in conn.execute("SELECT event_id FROM events")]
    conn.close()
    assert remaining == ["REAL"]


def test_script_several_covers_every_status(tmp_path):
    assert _run_script(["--several"], tmp_path).returncode == 0
    from ledsync.config import Config

    conn = connect(Config(data_dir=tmp_path).db_path)
    statuses = {r["status"] for r in conn.execute("SELECT status FROM events")}
    conn.close()
    assert {"Ready", "Synced", "Attention needed"} <= statuses


# --- BLOCKER 1 (architect review): odd timestamps must never crash the page -----------

HOSTILE = [
    "1969-12-31T23:59:59Z",        # before the epoch: OSError on Windows local-time conversion
    "0001-01-01T00:00:00Z",
    "1899-12-31T00:00:00Z",
    "1900-01-01T00:00:00Z",
    "9999-12-31T00:00:00Z",
    "9999-12-31T23:59:59-23:59",   # OverflowError
    "0001-01-01T00:00:00+14:00",
    "2100-01-01T00:00:00Z",        # two-digit year would be ambiguous
    "3001-01-01T00:00:00Z",
    "2026-09-08T16:45:00\x00Z",    # NUL byte
    "2026-09-08T16:45:60Z",        # leap second
    "2026-09-08T24:00:00Z",
    "NaN", "inf", "-1", "0", "12345678901234567890",
    "x" * 100_000,
    "\u2603 snowman", "2026-W37-1",
]


HOSTILE_IDS = [f"hostile-{i}" for i in range(len(HOSTILE))]


@pytest.mark.parametrize("stored", HOSTILE, ids=HOSTILE_IDS)
def test_no_stored_timestamp_can_raise_in_any_zone(stored):
    for tz in (None, PLUS3, timezone.utc):
        out = events.format_timestamp(stored, tz)
        assert isinstance(out, str) and out
    assert events.parse_timestamp(stored) is None or 1970 <= events.parse_timestamp(stored).year <= 2100


@pytest.mark.parametrize("stored", HOSTILE, ids=HOSTILE_IDS)
def test_dashboard_stays_up_with_a_hostile_row_next_to_a_good_one(app, cfg, launched, stored):
    conn = connect(cfg.db_path)
    _add(conn, "1000", "Good Event", "2026-09-18T07:02:26Z", "Ready")
    _add(conn, "666", "Odd Event", stored, "Synced")
    conn.close()
    resp = _dashboard(app, launched)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Good Event" in html and "Odd Event" in html      # the bad row never hides the good one


def test_out_of_range_dates_are_shown_raw_not_converted():
    assert events.format_timestamp("1969-12-31T23:59:59Z", PLUS3) == "1969-12-31T23:59:59Z"
    assert events.format_timestamp("2100-01-01T00:00:00Z", PLUS3) == "2100-01-01T00:00:00Z"
    assert events.parse_timestamp("1970-01-01T00:00:00Z") is not None
    assert events.parse_timestamp("2098-12-31T23:59:59Z") is not None
    assert events.parse_timestamp("2099-12-31T23:59:59Z") is None   # would show as year "00" in zones ahead of UTC


def test_blob_and_numeric_values_from_hand_edited_rows_are_coerced(conn):
    conn.execute("INSERT INTO events (event_id, event_name, last_updated, status) VALUES (?,?,?,?)",
                 ("b1", b"Blob Name", b"2026-09-08T16:45:00Z", b"ready"))
    conn.commit()
    [row] = events.list_events(conn, PLUS3)
    assert row.event_name == "Blob Name" and row.last_updated == "08/09/26 19:45"
    assert row.status == "Ready" and "b'" not in row.event_name + row.last_updated


def test_one_unprocessable_row_is_skipped_not_fatal(conn, monkeypatch, caplog):
    _add(conn, "1", "Fine", "2026-09-08T10:00:00Z")
    _add(conn, "2", "Breaks", "2026-09-09T10:00:00Z")
    real = events._build_row

    def flaky(r, tz):
        if r["event_id"] == "2":
            raise RuntimeError("boom")
        return real(r, tz)

    monkeypatch.setattr(events, "_build_row", flaky)
    with caplog.at_level("ERROR"):
        rows = events.list_events(conn, PLUS3)
    assert [r.event_id for r in rows] == ["1"]
    assert "Skipping an events row" in caplog.text


def test_rows_keep_their_raw_values_for_later_phases(conn):
    _add(conn, "1", "E", "2026-09-08T16:45:00Z", " ready ")
    [row] = events.list_events(conn, PLUS3)
    assert row.last_updated_raw == "2026-09-08T16:45:00Z" and row.status_raw == "ready"


def test_unexpected_server_error_shows_a_branded_page_not_a_stock_one(app, launched, monkeypatch):
    do_login(launched)

    def boom(*a, **k):
        raise RuntimeError("secret internal detail /path/to/db")

    monkeypatch.setattr(events, "list_events", boom)
    resp = launched.get("/")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 500
    assert "Something Went Wrong" in html and "wtt-logo.png" in html
    assert "secret internal detail" not in html and "Traceback" not in html
    assert "Internal Server Error" not in html


def test_table_region_is_keyboard_reachable_and_labelled(app, cfg, launched):
    conn = connect(cfg.db_path)
    _add(conn, "1", "E", "2026-09-08T10:00:00Z")
    conn.close()
    html = _dashboard(app, launched).get_data(as_text=True)
    assert 'tabindex="0"' in html and 'role="region"' in html and "aria-label=" in html


# --- dev script safety guards (architect review, finding 3) ----------------------------

def test_script_refuses_the_real_data_folder(tmp_path):
    real = tmp_path / "LEDAssetSync"
    real.mkdir()
    env = {**os.environ, "LOCALAPPDATA": str(tmp_path), "LEDSYNC_DATA_DIR": str(real)}
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "insert_test_event.py")],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 2 and "REAL data folder" in r.stderr
    assert not (real / "ledsync.db").exists()      # nothing was created there


def test_script_refuses_a_folder_that_does_not_exist_unless_create(tmp_path):
    missing = tmp_path / "typo-folder"
    assert _run_script([], missing).returncode == 2
    assert not missing.exists()                    # a typo must not silently create a folder
    assert _run_script(["--create"], missing).returncode == 0
    assert (missing / "ledsync.db").exists()


def test_script_never_overwrites_an_existing_event_with_the_same_id(tmp_path):
    from ledsync.config import Config

    assert _run_script(["--create"], tmp_path).returncode == 0     # creates the DB
    db = Config(data_dir=tmp_path).db_path
    conn = connect(db)
    conn.execute("DELETE FROM events")
    conn.execute("INSERT INTO events (event_id, event_name, event_guid, configuration_json) "
                 "VALUES ('1000', 'REAL EVENT', 'real-guid', '{\"keep\": true}')")
    conn.commit()
    r = _run_script([], tmp_path)
    row = conn.execute("SELECT event_name, event_guid, configuration_json, configuration_file "
                       "FROM events WHERE event_id = '1000'").fetchone()
    conn.close()
    assert r.returncode == 0 and "Skipped existing" in r.stdout
    assert tuple(row) == ("REAL EVENT", "real-guid", '{"keep": true}', None)   # untouched, not re-tagged


def test_text_that_is_not_valid_utf8_never_hides_the_list(app, cfg, launched, conn):
    """Re-check finding: invalid-UTF-8 TEXT made sqlite3 raise inside fetchall(), hiding every event."""
    _add(conn, "1000", "Good Event", "2026-09-18T07:02:26Z", "Ready")
    conn.execute("INSERT INTO events(event_id, event_name, last_updated, status) "
                 "VALUES ('bad', CAST(x'ff41' AS TEXT), CAST(x'fe' AS TEXT), CAST(x'fd' AS TEXT))")
    conn.commit()
    rows = events.list_events(conn, PLUS3)
    assert {r.event_id for r in rows} == {"1000", "bad"}
    assert conn.text_factory is str                         # connection setting restored afterwards
    do_login(launched)
    resp = launched.get("/")
    assert resp.status_code == 200 and "Good Event" in resp.get_data(as_text=True)


def test_year_2099_never_displays_as_00_in_a_zone_ahead_of_utc():
    assert events.format_timestamp("2099-12-31T23:59:59Z", PLUS3) == "2099-12-31T23:59:59Z"   # raw
