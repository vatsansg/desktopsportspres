"""Phase 6 - regression tests for the independent architect review findings (21/09/26)."""

import csv
import re
import sqlite3

import pytest
from conftest import csrf_from, db_rows, serve_event
from phase6_helpers import REAL_GUID_JSON, T0, T1, T2, add_history, csv_text, parsed, two_table_structure

from ledsync.db import connect, init_db
from ledsync.services import changelog, changes, exceptions, localchangelog

FOLDER = "1000 - Star contender Doha"


def compare(rows, local=None):
    return changes.compare(parsed(rows), two_table_structure(), local or {})


# --- finding 1: names that are unsafe on NTFS are refused before any later phase can use them ---------------------

UNSAFE = [
    "a.png.", "a.png ", "a.png:s", "a.png::$DATA", "NUL", "NUL.png", "nul.tar.gz", "com1.txt", "COM0", "PRN.",
    "AUX .txt", "CONIN$", "CONOUT$.log", "lpt\u00b2.txt", "a<b>.png", "a?b.png", "a*b.png", "a|b.png", 'a"b.png',
    "a" * 256 + ".png", " lead.png", "a\\b.png",
]


@pytest.mark.parametrize("name", UNSAFE, ids=lambda v: ascii(v)[:24])
def test_names_that_are_dangerous_on_windows_are_skipped_not_queued(name):
    text = csv_text([("Table 1/Inner/" + name, T0, "New"), ("Table 1/Inner/good.png", T0, "New")])
    result = changelog.parse_change_log(text.encode())
    assert [e.path for e in result.entries] == ["Table 1/Inner/good.png"]
    assert len(result.skipped) == 1 and name not in result.skipped[0].reason


@pytest.mark.parametrize("path", [
    "(ENG) Frankfurt.png", "Home Look.mp4", "a.b.c.png", "\u0645\u0644\u0639\u0628.png", "a&b.png", "it's.png", "50%.png",
    "a#1.png", "[x] (y) {z}.png", "a+b=c~d,e;f.png", "CONSOLE.png", "COM10.png", "AUXILIARY.txt", "nul-file.png", ".hidden",
    "sponsorsequence.csv", "HOME_Look",
], ids=lambda v: ascii(v)[:24])
def test_ordinary_real_world_names_are_still_accepted(path):
    assert len(parsed([("Table 1/Inner/" + path, T0, "New")]).entries) == 1


def test_a_path_is_never_trimmed_it_is_refused():
    text = 'sno,filename,changetimestamp,status\n1," Table 1/Inner/a.png",%s,New\n2,"Table 1/Inner/b.png ",%s,New\n' % (T0, T0)
    result = changelog.parse_change_log(text.encode())
    assert result.entries == () and len(result.skipped) == 2


def test_an_over_long_full_path_is_refused():
    assert parsed([("Table 1/Inner/" + "a" * 190, T0, "New")]).skipped
    assert parsed([("Table 1/Inner/" + "a" * 100, T0, "New")]).entries


# --- finding 2: one file written two ways is one entry, not several downloads -------------------------------------

@pytest.mark.parametrize("variant", ["Table 01/Inner/a.png", "Table 0001/Inner/a.png", "Table 0/Inner/a.png",
                                     "Table \u0661/Inner/a.png", "Table  1/Inner/a.png", "Table 1 /Inner/a.png"],
                         ids=lambda v: ascii(v)[:26])
def test_look_alike_table_folders_never_create_a_second_download_of_the_same_file(variant):
    result = compare([("Table 1/Inner/a.png", T0, "New"), (variant, T0, "New")])
    assert [a.path for a in result.with_action(changes.DOWNLOAD)] == ["Table 1/Inner/a.png"]


def test_table_numbers_are_ascii_digits_only():
    for bad in ("Table \u0661/Inner/x.png", "Table \uff11/Inner/x.png"):
        assert compare([(bad, T0, "New")]).assessments[0].action == changes.NOT_APPLICABLE


def test_the_same_file_in_different_ascii_case_is_one_entry_and_one_download():
    result = compare([("Table 1/Inner/A.png", T0, "New"), ("table 1/INNER/a.PNG", T1, "Updated")])
    assert result.distinct_paths == 1 and result.count(changes.DOWNLOAD) == 1


# --- finding 3: names Windows keeps apart are never merged ----------------------------------------------------------

@pytest.mark.parametrize("one,two", [
    ("Stra\u00dfe.png", "Strasse.png"), ("\u212a.png", "k.png"), ("\u03c2.png", "\u03c3.png"),
    ("\u00b5.png", "\u03bc.png"), ("\u017f.png", "s.png"), ("e\u0301.png", "\u00e9.png"),
], ids=["ss-eszett", "kelvin", "sigma", "micro", "long-s", "accent-forms"])
def test_names_that_could_collide_on_disk_are_listed_not_merged_or_queued(one, two):
    result = compare([("Table 1/Inner/" + one, T0, "New"), ("Table 1/Inner/" + two, T1, "Deleted")])
    assert result.distinct_paths == 2
    assert {a.action for a in result.assessments} == {changes.NOT_APPLICABLE}
    assert all("same file on this computer" in a.reason for a in result.assessments)


def test_a_non_ascii_name_on_its_own_is_an_ordinary_download():
    a = compare([("Table 1/Inner/Stra\u00dfe.png", T0, "New")]).assessments[0]
    assert a.action == changes.DOWNLOAD


def test_local_history_keys_ignore_ascii_case_only():
    key_a, key_b = changes.path_key(1, "Inner", "Stra\u00dfe.png"), changes.path_key(1, "Inner", "Strasse.png")
    assert key_a != key_b and changes.path_key(1, "Inner", "A.PNG") == changes.path_key(1, "Inner", "a.png")


# --- finding 4: a database error never empties the mirror -------------------------------------------------------------

@pytest.fixture
def conn(cfg):
    init_db(cfg.db_path)
    c = connect(cfg.db_path)
    c.execute("INSERT INTO events (event_id, event_name, event_guid) VALUES ('1000', 'x', 'eb0153b9-b74a-45e8-9f0f-b2c1a1b7b459')")
    c.commit()
    yield c
    c.close()


def test_a_database_error_leaves_the_existing_local_change_log_untouched(conn, cfg):
    add_history(conn, "1000", 1, "Inner", "a.png", T1)
    assert localchangelog.write(cfg.data_dir, conn)
    target = cfg.data_dir / "_localchangelog.csv"
    before = target.read_bytes()
    conn.execute("ALTER TABLE download_history RENAME TO gone")
    assert localchangelog.write(cfg.data_dir, conn) is False
    assert target.read_bytes() == before and len(before.splitlines()) == 2


def test_a_database_error_with_no_file_yet_still_creates_the_headers(conn, cfg):
    conn.execute("ALTER TABLE download_history RENAME TO gone")
    assert localchangelog.write(cfg.data_dir, conn) is True
    rows = list(csv.reader(open(cfg.data_dir / "_localchangelog.csv", encoding="utf-8-sig", newline="")))
    assert rows == [list(localchangelog.HEADERS)]


@pytest.mark.parametrize("name", ["\nA", " =1", "\uff1d1", "\u22121", "\uff20x", "\tx", "=x", "+x", "-x", "@x"])
def test_more_formula_starts_are_neutralised(conn, cfg, name):
    add_history(conn, "1000", 1, "Inner", name, T1)
    localchangelog.write(cfg.data_dir, conn)
    cell = list(csv.reader(open(cfg.data_dir / "_localchangelog.csv", encoding="utf-8-sig", newline="")))[1][4]
    assert cell.startswith("'") or cell[:1] == " "                  # a leading space cannot start a formula in Excel


def test_two_writers_never_share_a_temporary_file(conn, cfg):
    localchangelog.write(cfg.data_dir, conn)
    assert not list(cfg.data_dir.glob("*.tmp"))


# --- finding 5: an unexpected problem is a plain message, logged and audited ---------------------------------------------

def test_an_unreadable_history_row_is_ignored_not_fatal(conn):
    conn.execute("INSERT INTO download_history (event_id, file_name, table_number, led_type, source_timestamp, status) "
                 "VALUES ('1000', 'a.png', 'abc', 'Inner', ?, 'Success')", (T1,))
    conn.commit()
    assert changes.local_state(conn, "1000") == {}


@pytest.fixture
def ev(logged_in):
    serve_event(logged_in.application.extensions["test.azure"], dict(REAL_GUID_JSON))
    logged_in.post("/events/new", data={"event_id": "1000", "csrf_token": csrf_from(logged_in, "/events/new")})
    return logged_in


def azure_of(client):
    return client.application.extensions["test.azure"]


def put_log(client, text):
    azure_of(client).put("2026", f"{FOLDER}/_ledassetschangelog.csv", text)


def page(client):
    return client.get("/events/1000/changes").get_data(as_text=True)


def check(client):
    return client.post("/events/1000/changes/check", data={"csrf_token": csrf_from(client, "/events/1000/changes")},
                       follow_redirects=True)


def text_of(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_an_unexpected_exception_is_a_plain_message_and_is_logged_and_audited(ev, cfg, monkeypatch):
    put_log(ev, csv_text([("Table 1/Inner/a.png", T0, "New")]))

    def boom(*a, **k):
        raise RuntimeError("secret internal detail C:\\x")
    monkeypatch.setattr(changes, "compare", boom)
    resp = check(ev)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200 and "unexpected problem" in html and "secret internal" not in html
    assert [f["category"] for f in db_rows(cfg, "SELECT category FROM exception_log")] == ["Configuration"]
    assert db_rows(cfg, "SELECT status FROM operation_log WHERE operation = 'Check Changes'") == [{"status": "Failed"}]


def test_a_failure_to_log_never_hides_the_real_message(ev, cfg, monkeypatch):
    def broken(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(exceptions, "record", broken)
    resp = check(ev)                                   # no change log exists yet in the fake Azure
    assert resp.status_code == 200 and "no change log yet" in resp.get_data(as_text=True)


# --- finding 6: a saved result is dropped when it no longer describes reality --------------------------------------------

def test_the_result_is_dropped_when_the_event_is_registered_again(ev, cfg):
    put_log(ev, csv_text([("Table 1/Inner/a.png", T0, "New")]))
    check(ev)
    assert "Waiting To Be Processed (1)" in text_of(page(ev))
    conn = connect(cfg.db_path)
    conn.execute("UPDATE events SET event_guid = '7c9e6679-7425-40de-944b-e07fc1f90ae7' WHERE event_id = '1000'")
    conn.commit()
    conn.close()
    assert "No check has been run yet" in page(ev)


def test_the_result_is_dropped_when_the_local_history_changes(ev, cfg):
    put_log(ev, csv_text([("Table 1/Inner/a.png", T0, "New")]))
    check(ev)
    conn = connect(cfg.db_path)
    add_history(conn, "1000", 1, "Inner", "a.png", T0)
    conn.close()
    assert "No check has been run yet" in page(ev)


def test_an_unchanged_result_stays(ev):
    put_log(ev, csv_text([("Table 1/Inner/a.png", T0, "New")]))
    check(ev)
    assert "Waiting To Be Processed (1)" in text_of(page(ev))
    assert "Waiting To Be Processed (1)" in text_of(page(ev))


# --- notes ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("order", ["new-first", "deleted-first"])
def test_same_time_entries_are_settled_by_their_order_in_the_file(order):
    rows = [("Table 1/Inner/a.png", T1, "New"), ("Table 1/Inner/a.png", T1, "Deleted")]
    if order == "deleted-first":
        rows.reverse()
    a = compare(rows).assessments[0]
    assert a.cloud_status == rows[-1][2]                        # the later line wins, whatever the sno values are


def test_a_missing_sno_does_not_change_which_entry_wins():
    text = ("sno,filename,changetimestamp,status\n5,Table 1/Inner/a.png,%s,New\n,Table 1/Inner/a.png,%s,Deleted\n" % (T1, T1))
    a = changes.compare(changelog.parse_change_log(text.encode()), two_table_structure(), {}).assessments[0]
    assert a.cloud_status == "Deleted"


def test_a_removal_older_than_the_local_copy_says_the_copy_is_kept():
    from ledsync.services.events import parse_timestamp
    local = {changes.path_key(1, "Inner", "a.png"): changes.LocalRecord("Success", parse_timestamp(T2))}
    a = compare([("Table 1/Inner/a.png", T1, "Deleted")], local).assessments[0]
    assert a.action == changes.DONE and "kept" in a.reason


def test_removals_are_listed_before_downloads_so_a_long_list_cannot_hide_them(ev, cfg):
    rows = [(f"Table 1/Inner/f{i:04d}.png", T0, "New") for i in range(600)] + [("Table 1/Inner/zzz.png", T0, "New"),
                                                                                  ("Table 1/Inner/zzz.png", T2, "Deleted")]
    put_log(ev, csv_text(rows))
    conn = connect(cfg.db_path)
    add_history(conn, "1000", 1, "Inner", "zzz.png", T1)
    conn.close()
    text = text_of(check(ev).get_data(as_text=True))
    assert "Showing the first 500 of 601" in text and "the local copy will be deleted" in text


def test_entries_dated_far_in_the_future_are_pointed_out_but_still_compared(ev):
    put_log(ev, csv_text([("Table 1/Inner/a.png", "2090-01-01T00:00:00Z", "New"), ("Table 1/Inner/b.png", T0, "New")]))
    text = text_of(check(ev).get_data(as_text=True))
    assert "1 entry is dated more than a day in the future" in text and "Waiting To Be Processed (2)" in text


def test_no_future_notice_for_ordinary_entries(ev):
    put_log(ev, csv_text([("Table 1/Inner/a.png", T0, "New")]))
    assert "in the future" not in check(ev).get_data(as_text=True)
