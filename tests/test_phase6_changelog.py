"""Phase 6, Step 6.1 - reading the cloud change log (`_ledassetschangelog.csv`)."""

import pytest
from fakes import ACCOUNT, FAKE_KEY, FakeBlobService, http_error
from phase6_helpers import SAMPLE_LOG, T0, T1, csv_text, parsed

from ledsync.services import changelog, exceptions
from ledsync.services.storage import AzureReadOnlyStorage, EventLocation, StorageError

LOC = EventLocation("2026", "1000 - Star contender Doha")
GOOD = ("Table 1/Inner/a.png", T0, "New")


# --- the real exported file -------------------------------------------------------------------------------------

def test_the_real_exported_change_log_parses_completely():
    result = changelog.parse_change_log(SAMPLE_LOG.read_bytes())
    assert len(result.entries) == 83 and result.skipped == ()
    from collections import Counter
    assert Counter(e.status for e in result.entries) == {"New": 58, "Updated": 20, "Deleted": 5}
    assert {e.path for e in result.entries if e.path.endswith("sponsorsequence.csv")} == {
        "Table 1/Inner/sponsorsequence.csv", "Table 1/Outer/sponsorsequence.csv"}
    assert all(e.timestamp.tzinfo is not None for e in result.entries)


def test_the_full_path_is_kept_not_just_the_file_name():
    result = parsed([("Table 1/Inner/x.png", T0, "New"), ("Table 2/Outer/x.png", T1, "New")])
    assert [e.path for e in result.entries] == ["Table 1/Inner/x.png", "Table 2/Outer/x.png"]


# --- formats that differ between tools --------------------------------------------------------------------------

def test_bom_and_windows_line_endings_are_tolerated():
    data = b"\xef\xbb\xbf" + csv_text([GOOD, ("Table 1/Inner/b.png", T1, "Updated")]).replace("\n", "\r\n").encode()
    assert len(changelog.parse_change_log(data).entries) == 2


def test_quoted_names_with_commas_and_extra_or_reordered_columns():
    text = 'status,extra,filename,changetimestamp\nNew,zzz,"Table 1/Inner/a,b.png",%s\n' % T0
    result = changelog.parse_change_log(text.encode())
    assert result.entries[0].path == "Table 1/Inner/a,b.png" and result.entries[0].sno is None


def test_header_case_and_spaces_do_not_matter():
    text = " SNO , FileName ,ChangeTimestamp, STATUS \n1,Table 1/Inner/a.png,%s,new\n" % T0
    assert changelog.parse_change_log(text.encode()).entries[0].status == "New"


def test_blank_lines_are_ignored_and_a_missing_username_column_is_fine():
    text = "sno,filename,changetimestamp,status\n\n1,Table 1/Inner/a.png,%s,New\n\n" % T0
    result = changelog.parse_change_log(text.encode())
    assert len(result.entries) == 1 and result.skipped == ()


@pytest.mark.parametrize("stamp,expected_hour", [
    ("2026-09-17T06:00:00.000Z", 6), ("2026-09-17T06:00:00Z", 6), ("2026-09-17T09:00:00+03:00", 6),
    ("2026-09-17 06:00:00", 6),                                      # no zone: read as UTC
], ids=lambda v: str(v)[:22])
def test_timestamps_are_read_as_utc_instants(stamp, expected_hour):
    entry = parsed([("Table 1/Inner/a.png", stamp, "New")]).entries[0]
    assert entry.timestamp.utctimetuple().tm_hour == expected_hour
    assert entry.timestamp_text == stamp


def test_non_numeric_sno_is_tolerated():
    text = "sno,filename,changetimestamp,status\nabc,Table 1/Inner/a.png,%s,New\n" % T0
    entry = changelog.parse_change_log(text.encode()).entries[0]
    assert entry.sno is None


# --- a file that cannot be understood ---------------------------------------------------------------------------

@pytest.mark.parametrize("data,fragment", [
    (b"", "empty"),
    (b"\n\n", "empty"),
    (b"a,b,c\n1,2,3\n", "expected columns"),
    (b"sno,filename,status\n1,x,New\n", "expected columns"),
    (b"\xff\xfe\x00bad", "not valid text"),
    (b"sno,filename,changetimestamp,status\n1,a\x00b,2026-09-17T06:00:00Z,New\n", "not a readable CSV"),
    (b"x" * 5_000_001, "too large"),
], ids=["empty", "blank", "wrong-cols", "no-timestamp-col", "bad-utf8", "nul", "huge"])
def test_an_unusable_file_is_refused_with_a_plain_message(data, fragment):
    with pytest.raises(changelog.ChangeLogError) as err:
        changelog.parse_change_log(data)
    assert fragment in err.value.message and err.value.category == exceptions.CONFIGURATION


def test_too_many_rows_are_refused(monkeypatch):
    monkeypatch.setattr(changelog, "MAX_ROWS", 3)
    with pytest.raises(changelog.ChangeLogError) as err:
        parsed([GOOD] * 4)
    assert "more than 3 rows" in err.value.message


# --- rows that cannot be trusted are skipped and reported, the rest still used ----------------------------------

BAD_ROWS = [
    ("", T0, "New", "file name is empty"),
    ("../secret.txt", T0, "New", "not a valid relative path"),
    ("/abs/x.png", T0, "New", "not a valid relative path"),
    ("Table 1//x.png", T0, "New", "not a valid relative path"),
    ("Table 1\\Inner\\x.png", T0, "New", "not allowed"),
    ("Table 1/Inner/a\u202eb.png", T0, "New", "not allowed"),
    ("Table 1/Inner/a\u200bb.png", T0, "New", "not allowed"),
    ("Table 1/Inner/a\x07b.png", T0, "New", "not allowed"),
    ("Table 1/Inner/" + "a" * 1100, T0, "New", "too long"),
    ("Table 1/Inner/a.png", T0, "Removed", "status is not"),
    ("Table 1/Inner/a.png", T0, "", "status is not"),
    ("Table 1/Inner/a.png", "yesterday", "New", "change time"),
    ("Table 1/Inner/a.png", "1901-01-01T00:00:00Z", "New", "change time"),
    ("Table 1/Inner/a.png", "", "New", "change time"),
]


@pytest.mark.parametrize("name,stamp,status,fragment", BAD_ROWS, ids=[f"{i}-{r[3][:14]}" for i, r in enumerate(BAD_ROWS)])
def test_a_bad_row_is_skipped_reported_and_never_stops_the_rest(name, stamp, status, fragment):
    text = "sno,filename,changetimestamp,status\n1,\"Table 1/Inner/good.png\",%s,New\n2,\"%s\",%s,%s\n3,\"Table 1/Inner/good2.png\",%s,New\n" % (
        T0, name, stamp, status, T0)
    result = changelog.parse_change_log(text.encode())
    assert [e.path for e in result.entries] == ["Table 1/Inner/good.png", "Table 1/Inner/good2.png"]
    assert len(result.skipped) == 1 and result.skipped[0].line == 3 and fragment in result.skipped[0].reason
    assert name not in result.skipped[0].reason or name == ""          # never echoes the row's own content


def test_a_short_row_is_skipped():
    text = "sno,filename,changetimestamp,status\n1,Table 1/Inner/a.png\n"
    result = changelog.parse_change_log(text.encode())
    assert result.entries == () and "missing columns" in result.skipped[0].reason


# --- fetching (read-only) -----------------------------------------------------------------------------------------

def storage_for(fake):
    return AzureReadOnlyStorage(ACCOUNT, FAKE_KEY, service_factory=fake.factory)


@pytest.fixture
def fake():
    f = FakeBlobService()
    f.containers.add("2026")
    return f


def test_fetch_reads_the_real_file_name_first(fake):
    fake.put("2026", f"{LOC.folder}/_ledassetschangelog.csv", csv_text([GOOD]))
    fake.put("2026", f"{LOC.folder}/_ledassetchangelog.csv", csv_text([GOOD, GOOD]))
    result = changelog.fetch_change_log(storage_for(fake), LOC)
    assert result.source_name == "_ledassetschangelog.csv" and len(result.entries) == 1


def test_fetch_falls_back_to_the_brd_spelling(fake):
    fake.put("2026", f"{LOC.folder}/_ledassetchangelog.csv", csv_text([GOOD, GOOD]))
    result = changelog.fetch_change_log(storage_for(fake), LOC)
    assert result.source_name == "_ledassetchangelog.csv" and len(result.entries) == 2


def test_fetch_with_no_change_log_says_so(fake):
    with pytest.raises(changelog.ChangeLogError) as err:
        changelog.fetch_change_log(storage_for(fake), LOC)
    assert err.value.category == exceptions.MISSING_FOLDER and "no change log yet" in err.value.message


def test_fetch_passes_real_azure_problems_through_unchanged(fake):
    fake.fail_with = http_error(403)
    with pytest.raises(StorageError) as err:
        changelog.fetch_change_log(storage_for(fake), LOC)
    assert err.value.category == exceptions.PERMISSION


def test_fetch_never_holds_more_than_the_cap(fake):
    fake.put("2026", f"{LOC.folder}/_ledassetschangelog.csv", b"x" * 6_000_000)
    with pytest.raises(changelog.ChangeLogError) as err:
        changelog.fetch_change_log(storage_for(fake), LOC)
    assert "too large" in err.value.message
    assert fake.downloads[-1][3] == changelog.MAX_BYTES + 1          # the request itself is capped


def test_fetch_uses_only_read_operations(fake):
    fake.put("2026", f"{LOC.folder}/_ledassetschangelog.csv", csv_text([GOOD]))
    changelog.fetch_change_log(storage_for(fake), LOC)     # the fake raises if any write operation is attempted
    assert set(fake.calls) <= {"list_containers", "download"}
