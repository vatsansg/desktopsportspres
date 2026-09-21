# QA Test Case Document — Desktop Phase 3: Event Registration and GUID Validation

## Header

| Field | Value |
|---|---|
| Application | Desktop |
| Step / Phase | Phase 3 — Event Registration (Step 3.1, local test file) and GUID Validation (Step 3.2) |
| BRD reference(s) | Desktop BRD Sections 8 (registration), 9 / 9.2 (GUID verification), 10 (event configuration JSON), 11 (LED structure comes from the file), 21 / 21.1 (exception log), 25 (operation log), 26 |
| Implementation Sequence reference | Desktop Implementation Sequence Step 3.1, Step 3.2 |
| Date | 21/09/26 |
| Tested by | Claude Code (automated + rendered pages); user to run the manual guide below |
| Environment | Windows 11 Pro, Python 3.11.9, pywebview 6.2.1, WebView2 153.x; scratch database only. **No Azure access** — the `_GUID.json` is a local file chosen with a file picker (Phase 4 replaces this with the Azure download). |

## Owner decisions applied

- **File input:** a normal *Choose file* upload; the app never reads a path.
- **Re-registration:** an already-registered event that arrives with a **different GUID** is rejected and logged, then the operator is redirected to a **Re-Register** screen and must paste the *new GUID copied from the web application*; it must equal the GUID in the newly supplied file. Only then is the registration replaced.
- **Last Updated after registering** = the **export time** from `_GUID.json` (owner choice; BRD 7.2 wording says "latest asset update" — later phases overwrite it with real change-log data).
- Status starts as **Registered**.
- Technical safety cap of **100 tables per file** (to stop a hostile file); this is *not* the still-open business "maximum tables per event" decision.

## How to test in the real app (for the user)

Use a scratch data folder. Test files are in `docs\testfiles\phase3\` (see its README for the one-line purpose of each).

```powershell
cd C:\vatsan\techprojects\INFRA\ledassetmanagement\Applications\desktop
$env:LEDSYNC_DATA_DIR = "$env:TEMP\ledsync-test-p3"
New-Item -ItemType Directory -Force $env:LEDSYNC_DATA_DIR | Out-Null
.\.venv\Scripts\python -m ledsync
```

Sign in, then on the **Dashboard** use **ADD NEW EVENT**. In the file picker go to `C:\vatsan\techprojects\INFRA\ledassetmanagement\Applications\desktop\docs\testfiles\phase3`.

| # | Event ID | File | Expect | Covers |
|---|---|---|---|---|
| 1 | — | Look at the empty Dashboard | "No Events Registered Yet" and a single orange **ADD NEW EVENT** button | TC-M01 |
| 2 | `1000` | `1000_valid.json` | Back on the Dashboard with a green notice "Event 1000 (Star contender Doha) registered." Row: `1000`, Star contender Doha, **Last Updated = 18/09/26 + your local time** (UTC+8 → `15:02`), badge **Registered** | TC-M02 |
| 3 | `1000` | `1000_valid.json` again | Blue notice "already registered with this GUID. Nothing changed." No second row | TC-M03 |
| 4 | `2000` | `2000_second_event.json` | Registered (2 rows now, newest first) | TC-M04 |
| 5 | `2000` | `1000_valid.json` | Stays on the form with a red message: the file is for event **1000** but you entered **2000**. Your typed ID is kept. Nothing registered | TC-M05 |
| 6 | `2001` | `2001_duplicate_guid_of_1000.json` | Red message: this GUID already belongs to a different registered event (1000) | TC-M06 |
| 7 | `3000` | `bad_malformed.json` | Red message: not valid JSON | TC-M07 |
| 8 | `3001` | `bad_missing_event_name.json` | Red message naming `eventName` | TC-M07 |
| 9 | `3002` | `bad_no_tables.json` | Red message: at least one table | TC-M07 |
| 10 | `3003` | `bad_invalid_guid.json` | Red message naming `exportGuid` | TC-M07 |
| 11 | *(blank)* / `../x` / `a b` | any | Red message about the Event ID ("Enter the Event ID" / "letters, numbers…"). Try also submitting with **no file chosen** | TC-M08 |
| 12 | `1000` | `1000_reexported_new_guid.json` (event 1000 is already registered with a different GUID) | **Redirected to "Re-Register Event"**: it shows *GUID registered now* `eb0153b9-b74a-45e8-9f0f-b2c1a1b7b459` in full on one line, but the file's GUID only by its **ending** (`…f90ae7`) — on purpose, so you cannot simply copy it back; you must get the new GUID from the web application (for this test it is in the README: `7c9e6679-7425-40de-944b-e07fc1f90ae7`) | TC-M09 |
| 13 | — | On that screen paste the **old** GUID `eb0153b9-b74a-45e8-9f0f-b2c1a1b7b459` and submit | Red message (focus jumps to it): does not match the GUID in the file. Not re-registered. (Try `garbage` too: "not a valid GUID") | TC-M10 |
| 14 | — | Paste `7c9e6679-7425-40de-944b-e07fc1f90ae7` (lower or upper case, with or without spaces/braces) and submit | Dashboard notice "Event 1000 … re-registered with the new GUID." **Last Updated = 21/09/26** (+ local time), status **Registered** | TC-M11 |
| 15 | `1000` | `1000_valid.json` (now the *old* export) | Redirected to Re-Register again (it no longer matches). Click **Cancel — keep the current registration** → notice "cancelled", nothing changed | TC-M12 |
| 16 | — | Close the app (X) and relaunch with the same commands | Both events are still listed | TC-M13 |
| 17 | — | Open `%TEMP%\ledsync-test-p3\ledsync.db` in DB Browser for SQLite. **Browse Data → `events`**: the `configuration_json` column holds the file text; `event_guid` is lower-case. **`exception_log`**: one row per rejection above (categories *Configuration*, *GUID validation*, *Invalid configuration*), with the file name in `source` and status *Open*; typos (step 11) are **not** logged. **`operation_log`**: *Event Registered* ×2 and *Event Re-registered* ×1; the messages now include the GUID(s) and the file source (the re-registration row reads `GUID eb0153b9-… -> 7c9e6679-…`) | TC-M14 |
| 18 | — | Keyboard only on the Add form: Tab order Event ID → file picker → REGISTER EVENT → Cancel, visible focus ring; Enter in the Event ID field does not submit without a file | TC-M15 |

Reset: close the app, then `Remove-Item -Recurse -Force "$env:TEMP\ledsync-test-p3"`.

## Summary

| Total cases | Passed | Failed | Blocked | Not yet run |
|---|---|---|---|---|
| 56 | 41 (38 automated TC-A01 – TC-A38 covering 211 new pytest cases, + 3 rendered-page TC-R01 – TC-R03) | 0 | 0 | 15 manual (TC-M01 – TC-M15, awaiting the user's run) |

`.\.venv\Scripts\python -m pytest -q` → **418 passed** (Phases 0–3 combined; 211 new; TC-A31 – TC-A38 were added after the independent architect review). Screens were rendered from the app's real templates and viewed in Chrome (`docs/screenshots/phase3-*.jpg`); no password was typed into a browser.

## Test cases — automated (`tests/test_registration.py`, `tests/test_event_routes.py`)

| ID | Description | Expected | Status |
|---|---|---|---|
| TC-A01 | The **real** exported test event (1000, Star contender Doha) parses: name, GUID, export time, tables (T1 Inner+Outer+Main, T2 Inner) exactly as the file says (BRD 10/11) | Match | Pass |
| TC-A02 | UTF-8 BOM tolerated; unknown extra fields ignored; numeric `eventId` accepted as text; missing/invalid export time is not fatal (stored blank) | As expected | Pass |
| TC-A03 | GUID normalisation: case, braces, whitespace accepted; no-hyphen, URN, short, long, nil (all zeros), non-hex, non-string rejected | Table of 14 cases | Pass |
| TC-A04 | `validate_guid` (BRD 9.2) matches ignoring case/braces and **fails closed** on missing/malformed/nil on either side | As expected | Pass |
| TC-A05 | 14 unusable files rejected: empty, not JSON, truncated, invalid UTF-8, list/string/null top level, `NaN`/`Infinity`, **duplicate keys**, 50 000-level nesting, oversize | `Invalid configuration` | Pass |
| TC-A06 | Each required field is required; 28 malformed-field variants rejected (bad event ID incl. `../x`, control characters, over-long values, non-https / credentials-in-URL storage URL, bad GUID, non-list/empty tables, booleans as 1/0 or "yes", `tableNumber` as text/bool/0/negative/1.5/10 000, duplicate tables) | Rejected with safe message | Pass |
| TC-A07 | 100-table safety cap: 100 accepted, 101 rejected; a table with no LED type enabled is accepted exactly as the file says | As expected | Pass |
| TC-A08 | Error messages never echo file content (hostile strings in name/GUID/URL/tables) | Not echoed | Pass |
| TC-A09 | Typed Event ID validation (16 cases incl. `../etc`, `1000/x`, spaces, full-width digits, `<script>`) | As expected | Pass |
| TC-A10 | **Step 3.1:** a valid file creates the `events` row: ID, name, lower-case GUID, source, `configuration_json` verbatim, Last Updated = export time, status Registered, no download/sync times; `Event Registered` in the operation log | Row + log | Pass |
| TC-A11 | Event ID in the file must equal the ID entered | Rejected (Configuration) | Pass |
| TC-A12 | Same event + same GUID → harmless no-op (nothing changed, nothing logged) | `already_registered` | Pass |
| TC-A13 | **Step 3.2:** existing event + different GUID → rejected as `GuidMismatch` carrying both GUIDs; nothing changed | Rejected | Pass |
| TC-A14 | A GUID cannot be reused by a different event (case/brace variants in stored values included) | Rejected (GUID validation) | Pass |
| TC-A15 | A hand-inserted event with no recorded GUID cannot be matched (fails closed) | Mismatch | Pass |
| TC-A16 | **Step 3.2 validation:** a GUID rejection is written to `exception_log` (category *GUID validation*, operation, event, source, both GUIDs, status *Open*, UTC time) | Row present | Pass |
| TC-A17 | Form typos are **not** logged as exceptions; exception rows carry only safe text; unknown category refused; all ten BRD §21 categories exist; a failed log write never raises | As expected | Pass |
| TC-A18 | Re-register replaces GUID/name/config/Last Updated/status when the pasted GUID matches (case/space/brace tolerant); `Event Re-registered` logged | Replaced | Pass |
| TC-A19 | Re-register refuses a pasted GUID ≠ the file's (logged) and a malformed paste (not logged); requires the event to exist; still refuses a GUID owned by another event; checks Event ID | Refused | Pass |
| TC-A20 | Existing device mappings are left untouched by re-registration for now (decision revisited at Step 5.2) | 1 mapping kept | Pass |
| TC-A21 | Pending re-registration store: expires, bounded (oldest dropped), discardable, unknown/garbage tokens → nothing, tokens unguessable and distinct | As expected | Pass |
| TC-A22 | All shipped test files in `docs/testfiles/phase3` behave exactly as their README says | As documented | Pass |
| TC-A23 | Scope guard: `registration.py` never touches `application_settings` or the admin credential | Clean | Pass |
| TC-A24 | Pages are login- and launch-gated; Add form is branded/accessible (labels, `enctype`, CSRF, `accept=.json`); Dashboard has exactly one primary orange action | As expected | Pass |
| TC-A25 | **End to end (Step 3.1):** upload registers the event; Dashboard shows the row, "Registered" badge, Last Updated in local time, success notice; two events side by side | Row visible | Pass |
| TC-A26 | **End to end (Step 3.2):** mismatch → 302 to Re-Register, event unchanged, exception row; page shows both GUIDs; matching paste completes and is single-use; wrong/malformed pastes refused (one logged, one not) with retry possible; Cancel discards | As expected | Pass |
| TC-A27 | Expired pending request cannot be completed; another session cannot use it | Redirect / 403 | Pass |
| TC-A28 | 6 rejection scenarios each show a clear message, keep the typed ID, register nothing and log the right category; typos/no-file are not logged | As expected | Pass |
| TC-A29 | *Security:* every POST needs CSRF; 200 KB upload → branded 413; just-over-limit file rejected; a "path" field is never read | Blocked | Pass |
| TC-A30 | *Security:* hostile event name and typed Event ID are escaped everywhere; file contents never echoed in errors or the exception log; client file name sanitised before being recorded; session cookie holds only an opaque token (no GUID/name) | As expected | Pass |
| TC-A31 | **Review 1 — lone surrogates:** `"a\ud800b"` in `eventName` or `eventStorageUrl` gives a clean 400 and an exception-log row (previously a 500 with no log); nothing reaches the database | Rejected | Pass |
| TC-A32 | **Review 2 — spoofing characters:** control characters (incl. C1, DEL, tab), line/paragraph separators and bidi override/embedding/isolate characters (e.g. `U+202E`) are rejected; **legitimate** Arabic, Persian (with ZWNJ), Chinese, accented, emoji, ZWJ and LRM/RLM names are accepted | Table of 11 + 7 | Pass |
| TC-A33 | **Review 3 — the pasted GUID must come from the web app:** the file's GUID is never shown in full (only its last 6 characters); the registered GUID is | Not in page | Pass |
| TC-A34 | **Review 4 — two browser tabs:** a stale tab cannot act on a newer pending request (form carries a page token that must equal the session's); a stale Cancel doesn't discard the current request; a new mismatch replaces (never orphans) the old pending entry; a paste without the token is refused; the current tab still works | As expected | Pass |
| TC-A35 | **Review 5 — audit detail:** registration rows record GUID and source; the re-registration row records old GUID → new GUID and source | Present | Pass |
| TC-A36 | **Review 6 — all-or-nothing:** if the audit row cannot be written, the registration/re-registration is rolled back (no event without its audit trail) and the operator sees a plain "could not be saved" message | Rolled back | Pass |
| TC-A37 | Export time is normalised to UTC at the door (offsets, naive, date-only, milliseconds); out-of-range/absent → blank. Re-registration also clears `last_download`/`last_sync` and resets status (nothing has come from the new export yet) | As expected | Pass |
| TC-A38 | Accessibility: on an error the message is focused (Add/Re-register pages only — the Phase 1 sign-in still autofocuses Username), field not auto-focused; fields linked to their hints and to the error; wording after completion says "expired or was already completed" | As expected | Pass |

## Rendered-page checks (21/09/26)

| ID | Description | Result |
|---|---|---|
| TC-R01 | Add New Event form: bordered inputs, styled file picker, one orange button | Pass |
| TC-R02 | Re-Register screen: registered GUID on **one line** (fixed in the polish pass); error state. The screenshots `docs/screenshots/phase3-reregister*.jpg` were re-taken after review fix 3 and show the file's GUID by its ending only. | Pass |
| TC-R03 | Dashboard after registration: success notice, populated table, Add New Event button | Pass |

## Open defects / follow-ups

| ID | Description | Severity | Status |
|---|---|---|---|
| F-17 | Manual checks TC-M01 – TC-M15 pending the owner's run. | Info | Awaiting user |
| F-18 | **Mappings on re-registration:** device mappings are currently left alone; whether to keep, clear or re-validate them when tables change is raised at Step 5.2. | Info | Carried to Phase 5 |
| F-19 | **Last Updated** now shows the *export* time at registration (owner choice), not an asset update; Phase 6 replaces it from the change log. | Info | By design |
| F-20 | The 100-table cap is a safety limit only; the business "maximum tables per event" remains open (BRD §36). | Info | Open |
| F-21 | The pending-file store is in memory: a restart between the rejection and the paste means adding the event again (message shown). | Low | Accepted |
| F-22 | **Phase 4:** `eventStorageUrl` is only checked to be an https URL; before any download it must be required to point at the configured storage account (an untrusted file must not steer the download, or a credential, to another host). | Medium | Carried to Phase 4 |
| F-23 | **Phase 4/5:** Event IDs are case-sensitive here (`abc` ≠ `ABC`, `01000` ≠ `1000`) but Windows folders are not — decide the rule before folders are created. Phase 5 must re-read the stored config through `parse_event_config`, not a bare `json.loads`. | Low | Carried forward |
| F-24 | GUID uniqueness across events is checked before insert but has no database unique index (two simultaneous registrations on one machine are very unlikely). Adding one needs a schema change — bundle with the migration runner. | Low | Carried to Phase 13 |
| F-25 | The rejection logger hard-codes operation names; Phase 8 (per-download GUID re-validation) will widen it to carry file/table/LED type. | Info | Carried to Phase 8 |
| F-26 | Only these six review items were fixed before hand-off; the fixes were verified by 45 new tests but did **not** get a second independent review pass (the first review approved with notes and no blockers). Say if you want one. | Info | Noted |
| F-12 | Earlier carry-forwards unchanged (single-instance lock, settings service excluding `admin_*`, log retention, migration runner, headless bootstrap, data-dir decision). | — | Carried forward |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context) | 21/09/26 | **Approved with notes** — no blockers. Ran 373 tests and probed the running app with its own scripts. Six "fix now" findings resolved (surrogate crash, spoofing characters, copy-back weakness, two-tab confusion, audit detail, transactionality — TC-A31 – TC-A38); notes carried forward as F-22 – F-25 |
| User (Vatsan) go-ahead | | | Pending |
