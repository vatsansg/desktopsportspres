"""Phase 6, Steps 6.2 and 6.3 - the local change log and the incremental comparison."""

import csv

import pytest
from phase6_helpers import T0, T1, T2, T3, add_history, parsed, real_structure, two_table_structure

from ledsync.config import Config
from ledsync.db import connect, init_db
from ledsync.services import changes, localchangelog, structure
from ledsync.services import registration as reg
from ledsync.web import create_app


@pytest.fixture
def conn(cfg):
    init_db(cfg.db_path)
    c = connect(cfg.db_path)
    c.execute("INSERT INTO events (event_id, event_name, event_guid) VALUES ('1000', 'Star contender Doha', "
              "'eb0153b9-b74a-45e8-9f0f-b2c1a1b7b459')")
    c.commit()
    yield c
    c.close()


def compare(rows, local=None, struct=None):
    return changes.compare(parsed(rows), struct or two_table_structure(), local or {})


def by_path(comparison):
    return {a.path: a for a in comparison.assessments}


def rec(status, stamp):
    from ledsync.services.events import parse_timestamp
    return changes.LocalRecord(status, parse_timestamp(stamp))


# --- Step 6.1 validation case: four distinct sponsorsequence.csv ------------------------------------------------

def test_sponsorsequence_in_both_tables_and_both_led_types_is_four_distinct_entries():
    rows = [(f"Table {t}/{led}/sponsorsequence.csv", T0, "Updated") for t in (1, 2) for led in ("Inner", "Outer")]
    result = compare(rows)
    assert result.distinct_paths == 4 and result.total_entries == 4
    assert sorted(a.path for a in result.assessments) == sorted(r[0] for r in rows)
    assert all(a.action == changes.DOWNLOAD for a in result.assessments)
    assert {(a.table, a.led_type) for a in result.assessments} == {(1, "Inner"), (1, "Outer"), (2, "Inner"), (2, "Outer")}


# --- Step 6.3 validation case: only the updated one is queued ---------------------------------------------------

def test_an_update_in_one_table_and_led_folder_does_not_queue_or_mask_the_others():
    names = [f"Table {t}/{led}/sponsorsequence.csv" for t in (1, 2) for led in ("Inner", "Outer")]
    local = {changes.path_key(t, led, "sponsorsequence.csv"): rec("Success", T1)
             for t in (1, 2) for led in ("Inner", "Outer")}
    rows = [(n, T0, "New") for n in names] + [("Table 1/Inner/sponsorsequence.csv", T2, "Updated")]
    result = compare(rows, local)
    queued = [a.path for a in result.with_action(changes.DOWNLOAD)]
    assert queued == ["Table 1/Inner/sponsorsequence.csv"]
    assert result.count(changes.DONE) == 3
    assert by_path(result)["Table 1/Inner/sponsorsequence.csv"].label == changes.LABEL_UPDATED


def test_a_mix_of_new_updated_and_already_processed():
    local = {changes.path_key(1, "Inner", "done.png"): rec("Success", T1),
             changes.path_key(1, "Inner", "old.png"): rec("Success", T0)}
    result = compare([("Table 1/Inner/done.png", T1, "New"), ("Table 1/Inner/old.png", T2, "Updated"),
                      ("Table 1/Inner/brand-new.png", T2, "New")], local)
    got = {a.file_name: (a.action, a.label) for a in result.assessments}
    assert got == {"done.png": (changes.DONE, changes.DONE), "old.png": (changes.DOWNLOAD, changes.LABEL_UPDATED),
                   "brand-new.png": (changes.DOWNLOAD, changes.LABEL_NEW)}


# --- rules of the comparison -----------------------------------------------------------------------------------------

def test_only_the_latest_entry_for_a_path_counts_whatever_order_the_file_lists_them_in():
    rows = [("Table 1/Inner/a.png", T2, "Updated"), ("Table 1/Inner/a.png", T0, "New"), ("Table 1/Inner/a.png", T1, "Updated")]
    a = compare(rows).assessments[0]
    assert a.cloud_time_text == T2 and a.revisions == 3 and a.cloud_status == "Updated"


def test_paths_match_ignoring_case_like_windows_folders_do():
    local = {changes.path_key(1, "Inner", "FFTT.png"): rec("Success", T1)}
    result = compare([("table 1/inner/fftt.PNG", T1, "New")], local)
    assert result.assessments[0].action == changes.DONE
    assert len(compare([("Table 1/Inner/A.png", T0, "New"), ("table 1/INNER/a.PNG", T1, "Updated")]).assessments) == 1


def test_equal_timestamps_are_already_processed_and_later_ones_are_not():
    local = {changes.path_key(1, "Inner", "a.png"): rec("Success", T1)}
    assert compare([("Table 1/Inner/a.png", T1, "Updated")], local).assessments[0].action == changes.DONE
    assert compare([("Table 1/Inner/a.png", T2, "Updated")], local).assessments[0].action == changes.DOWNLOAD


def test_times_are_compared_as_instants_not_as_text():
    local = {changes.path_key(1, "Inner", "a.png"): rec("Success", "2026-09-17T09:00:00+03:00")}     # = 06:00 UTC
    assert compare([("Table 1/Inner/a.png", "2026-09-17T06:00:00.000Z", "Updated")], local).assessments[0].action == changes.DONE
    assert compare([("Table 1/Inner/a.png", "2026-09-17T06:00:00.001Z", "Updated")], local).assessments[0].action == changes.DOWNLOAD


def test_a_cloud_deletion_after_a_successful_download_queues_a_local_delete():
    local = {changes.path_key(1, "Inner", "a.png"): rec("Success", T0)}
    a = compare([("Table 1/Inner/a.png", T0, "New"), ("Table 1/Inner/a.png", T1, "Deleted")], local).assessments[0]
    assert a.action == changes.DELETE and a.label == changes.LABEL_REMOVED and "deleted" in a.reason


def test_a_deletion_with_nothing_stored_locally_needs_no_action():
    a = compare([("Table 1/Inner/a.png", T0, "New"), ("Table 1/Inner/a.png", T1, "Deleted")]).assessments[0]
    assert a.action == changes.DONE and "nothing is stored locally" in a.reason


def test_a_processed_deletion_is_not_repeated_and_a_later_re_add_comes_back():
    local = {changes.path_key(1, "Inner", "a.png"): rec("Deleted", T1)}
    assert compare([("Table 1/Inner/a.png", T1, "Deleted")], local).assessments[0].action == changes.DONE
    back = compare([("Table 1/Inner/a.png", T1, "Deleted"), ("Table 1/Inner/a.png", T2, "New")], local).assessments[0]
    assert back.action == changes.DOWNLOAD and "again" in back.reason


def test_an_older_deletion_does_not_remove_a_newer_download():
    local = {changes.path_key(1, "Inner", "a.png"): rec("Success", T2)}
    assert compare([("Table 1/Inner/a.png", T1, "Deleted")], local).assessments[0].action == changes.DONE


# --- entries that do not apply to this event --------------------------------------------------------------------------

@pytest.mark.parametrize("path,fragment", [
    ("Other/HOME_Look.png", "not inside a Table"),
    ("RPI/sub/x.png", "sub-folders of RPI"),
    ("HOME_Look.png", "not inside a Table / LED-type folder"),
    ("Table 1/Inner/sub/x.png", "sub-folders"),
    ("Table 9/Inner/x.png", "Table 9 Inner is not part of this event"),
    ("Table 2/MainLED/x.png", "Table 2 Main LED is not part of this event"),
    ("Table 1/Bogus/x.png", "not a known LED type"),
    ("Tables 1/Inner/x.png", "not inside a Table folder"),
    ("Table one/Inner/x.png", "not inside a Table folder"),
    ("Table 1/Main LED/x.png", "Table 1 Main LED is not part of this event"),
], ids=lambda v: v[:22])
def test_entries_outside_the_events_tables_and_led_types_are_listed_but_never_acted_on(path, fragment):
    result = changes.compare(parsed([(path, T0, "New")]), two_table_structure(), {})
    a = result.assessments[0]
    assert a.action == changes.NOT_APPLICABLE and fragment in a.reason and a.table is None


@pytest.mark.parametrize("folder", ["Inner", "inner", "INNER"])
def test_led_folder_case_does_not_matter(folder):
    a = compare([(f"Table 1/{folder}/x.png", T0, "New")]).assessments[0]
    assert a.action == changes.DOWNLOAD and a.led_type == "Inner"


@pytest.mark.parametrize("folder", ["MainLED", "mainled", "Main LED", "main_led"])
def test_main_led_spellings_are_recognised(folder):
    a = changes.compare(parsed([(f"Table 1/{folder}/x.png", T0, "New")]), real_structure(), {}).assessments[0]
    assert a.action == changes.DOWNLOAD and a.led_type == "MainLED"


def test_table_numbers_with_a_leading_zero_are_refused_not_treated_as_a_second_copy():
    a = compare([("Table 01/Inner/x.png", T0, "New")]).assessments[0]
    assert a.action == changes.NOT_APPLICABLE and "leading zero" in a.reason


@pytest.mark.parametrize("path", ["Table 1/MainLED/HOME_Look", "Table 1/Inner/.hidden", "RPI/README"], ids=lambda v: v[-12:])
def test_a_file_with_no_extension_is_not_an_asset(path):
    a = changes.compare(parsed([(path, T0, "New")]), real_structure(), {}).assessments[0]
    assert a.action == changes.NOT_APPLICABLE and "no extension" in a.reason


def test_rpi_files_are_their_own_destination():
    a = changes.compare(parsed([("RPI/HOME_Look.png", T0, "New")]), real_structure(), {}).assessments[0]
    assert (a.action, a.led_type, a.table, a.file_name) == (changes.DOWNLOAD, "RPI", None, "HOME_Look.png")


def test_results_are_ordered_by_table_led_and_name_with_not_applicable_last():
    rows = [("RPI/a.png", T0, "New"), ("Table 2/Inner/b.png", T0, "New"), ("Table 1/Outer/a.png", T0, "New"),
            ("Table 1/Inner/z.png", T0, "New"), ("Table 1/Inner/A.png", T0, "New")]
    assert [a.path for a in compare(rows).assessments] == [
        "Table 1/Inner/A.png", "Table 1/Inner/z.png", "Table 1/Outer/a.png", "Table 2/Inner/b.png", "RPI/a.png"]


def test_the_comparison_carries_the_unreadable_rows_and_counts():
    text = "sno,filename,changetimestamp,status\n1,Table 1/Inner/a.png,%s,New\n2,,%s,New\n" % (T0, T0)
    from ledsync.services.changelog import parse_change_log
    result = changes.compare(parse_change_log(text.encode()), two_table_structure(), {})
    assert result.total_entries == 1 and len(result.skipped) == 1


# --- what "local" means (the database record) ---------------------------------------------------------------------------

def test_local_state_uses_successful_downloads_and_deletions_only(conn):
    add_history(conn, "1000", 1, "Inner", "ok.png", T1)
    add_history(conn, "1000", 1, "Inner", "failed.png", T1, status="Failure")
    add_history(conn, "1000", 1, "Inner", "gone.png", T2, status="Deleted")
    state = changes.local_state(conn, "1000")
    assert set(state) == {changes.path_key(1, "inner", "ok.png"), changes.path_key(1, "inner", "gone.png")}
    assert state[changes.path_key(1, "Inner", "gone.png")].status == "Deleted"


def test_a_failed_download_does_not_count_as_processed(conn):
    add_history(conn, "1000", 1, "Inner", "a.png", T2, status="Failure")
    result = changes.compare(parsed([("Table 1/Inner/a.png", T1, "New")]), two_table_structure(), changes.local_state(conn, "1000"))
    assert result.assessments[0].action == changes.DOWNLOAD


def test_the_latest_record_wins_and_the_event_lookup_ignores_case(conn):
    add_history(conn, "1000", 1, "Inner", "a.png", T1)
    add_history(conn, "1000", 1, "Inner", "a.png", T3)
    add_history(conn, "1000", 1, "Inner", "a.png", T0)              # an older row inserted later never lowers it
    assert changes.local_state(conn, "1000")[changes.path_key(1, "Inner", "a.png")].source_time.hour == 9
    assert changes.local_state(conn, "1000".upper())


def test_unreadable_history_rows_are_ignored_not_fatal(conn):
    add_history(conn, "1000", 1, "Inner", "a.png", "garbage")
    add_history(conn, "1000", 1, "Weird", "b.png", T1)
    add_history(conn, "1000", None, "Inner", "c.png", T1)
    assert changes.local_state(conn, "1000") == {}


def test_other_events_history_is_not_used(conn):
    conn.execute("INSERT INTO events (event_id, event_name) VALUES ('2000', 'Other')")
    conn.commit()
    add_history(conn, "2000", 1, "Inner", "a.png", T3)
    assert changes.local_state(conn, "1000") == {}


def test_comparing_changes_nothing_in_the_database(conn):
    add_history(conn, "1000", 1, "Inner", "a.png", T1)
    before = [tuple(r) for r in conn.execute("SELECT * FROM download_history")]
    changes.compare(parsed([("Table 1/Inner/a.png", T2, "Updated")]), two_table_structure(), changes.local_state(conn, "1000"))
    assert [tuple(r) for r in conn.execute("SELECT * FROM download_history")] == before


# --- Step 6.2 - the local change log file ---------------------------------------------------------------------------------

HEADERS = ["Serial Number", "Event ID", "Table", "LED Type", "File Name", "Source Location", "Local Location",
           "Timestamp", "Source Updated Timestamp", "Download Status", "Sync Status"]


def read_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle))


def test_the_file_is_created_at_start_up_in_the_data_folder_with_the_brd_columns(cfg):
    init_db(cfg.db_path)
    create_app(cfg)
    target = cfg.data_dir / "_localchangelog.csv"
    assert target.exists() and read_rows(target) == [HEADERS]                # initially empty


def test_start_up_never_creates_a_database_just_to_write_the_file(cfg):
    assert not cfg.db_path.exists()
    create_app(cfg)
    assert not cfg.db_path.exists() and read_rows(cfg.data_dir / "_localchangelog.csv") == [HEADERS]


def test_the_file_mirrors_the_database_history(conn, cfg):
    add_history(conn, "1000", 1, "MainLED", "a.png", T1)
    add_history(conn, "1000", 2, "Inner", "b.mp4", T2, status="Failure")
    assert localchangelog.write(cfg.data_dir, conn)
    rows = read_rows(cfg.data_dir / "_localchangelog.csv")
    assert rows[0] == HEADERS and len(rows) == 3
    one = dict(zip(HEADERS, rows[1]))
    assert (one["Event ID"], one["Table"], one["LED Type"], one["File Name"], one["Download Status"]) == (
        "1000", "Table 1", "Main LED", "a.png", "Success")
    assert one["Source Updated Timestamp"] == T1
    import re
    assert re.fullmatch(r"\d\d/\d\d/\d\d \d\d:\d\d", one["Timestamp"])                  # DD/MM/YY HH:MM, local time
    assert dict(zip(HEADERS, rows[2]))["Download Status"] == "Failure"


def test_file_names_that_look_like_formulas_are_neutralised(conn, cfg):
    add_history(conn, "1000", 1, "Inner", "=HYPERLINK(\"http://x\")", T1)
    add_history(conn, "1000", 1, "Inner", "@cmd", T1)
    add_history(conn, "1000", 1, "Inner", "+1", T1)
    add_history(conn, "1000", 1, "Inner", "-1", T1)
    localchangelog.write(cfg.data_dir, conn)
    names = [r[4] for r in read_rows(cfg.data_dir / "_localchangelog.csv")[1:]]
    assert names == ["'=HYPERLINK(\"http://x\")", "'@cmd", "'+1", "'-1"]


def test_a_rewrite_is_atomic_and_leaves_no_temporary_file(conn, cfg):
    localchangelog.write(cfg.data_dir, conn)
    add_history(conn, "1000", 1, "Inner", "a.png", T1)
    localchangelog.write(cfg.data_dir, conn)
    assert sorted(p.name for p in cfg.data_dir.glob("_localchangelog*")) == ["_localchangelog.csv"]
    assert len(read_rows(cfg.data_dir / "_localchangelog.csv")) == 2


def test_a_file_that_cannot_be_replaced_is_reported_not_fatal(conn, cfg):
    target = cfg.data_dir / "_localchangelog.csv"
    target.mkdir()                                              # something else is in the way
    assert localchangelog.write(cfg.data_dir, conn) is False
    assert not list(cfg.data_dir.glob("*.tmp"))


def test_a_file_open_in_another_program_keeps_the_previous_version(conn, cfg):
    localchangelog.write(cfg.data_dir, conn)
    target = cfg.data_dir / "_localchangelog.csv"
    before = target.read_bytes()
    add_history(conn, "1000", 1, "Inner", "a.png", T1)
    with open(target, "rb"):                                    # Windows will not replace a file that is open
        assert localchangelog.write(cfg.data_dir, conn) is False
    assert target.read_bytes() == before and not list(cfg.data_dir.glob("*.tmp"))
    assert localchangelog.write(cfg.data_dir, conn) is True     # and it works again once the file is closed
    assert len(read_rows(target)) == 2


def test_refresh_repairs_a_deleted_file_from_the_database(conn, cfg):
    add_history(conn, "1000", 1, "Inner", "a.png", T1)
    localchangelog.refresh(Config(data_dir=cfg.data_dir))
    assert len(read_rows(cfg.data_dir / "_localchangelog.csv")) == 2
    (cfg.data_dir / "_localchangelog.csv").unlink()
    create_app(cfg)
    assert len(read_rows(cfg.data_dir / "_localchangelog.csv")) == 2


def test_the_header_row_is_exactly_the_brd_section_17_list():
    assert list(localchangelog.HEADERS) == HEADERS and localchangelog.FILENAME == "_localchangelog.csv"
