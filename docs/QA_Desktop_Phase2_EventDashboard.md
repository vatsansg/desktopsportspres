# QA Test Case Document — Desktop Phase 2: Event Dashboard

## Header

| Field | Value |
|---|---|
| Application | Desktop |
| Step / Phase | Phase 2 — Event Dashboard, empty state and populated (Step 2.1) |
| BRD reference(s) | Desktop BRD Section 7.1 (event information), 7.2 (Event Dashboard: Event ID, Event Name, Last Updated, Status; DD/MM/YY HH:MM 24-hour), 26 (`events` table) |
| Implementation Sequence reference | Desktop Implementation Sequence Step 2.1 |
| Date | 21/09/26 |
| Tested by | Claude Code (automated + rendered pages); user to run the manual guide below |
| Environment | Windows 11 Pro, Python 3.11.9, pywebview 6.2.1, WebView2 153.x; scratch database only. No Azure access used. |

## Owner decisions applied (20/09/26)

- **Time zone:** *Last Updated* is shown in the venue machine's **local time**; stored as UTC ISO. (Display only — the open BRD §36 cut-off/comparison decision is untouched and is still asked at Step 6.3.)
- **Status vocabulary:** **Registered, Ready, Synced, Attention needed** (blank = Registered; unknown text shown as-is with a neutral badge). This differs from the BRD 33.4 operation-status wording by owner choice; later phases must map to it.
- Most recently updated first; undated rows last. No Add Event button, row click-through, search or pagination in this phase.

## How to test in the real app (for the user)

Use a scratch data folder so your real data is never touched.

```powershell
cd C:\vatsan\techprojects\INFRA\ledassetmanagement\Applications\desktop
$env:LEDSYNC_DATA_DIR = "$env:TEMP\ledsync-test"
```

| Step | Do this | Expect | Covers |
|---|---|---|---|
| 1 | `.\.venv\Scripts\python -m ledsync`, sign in (`admin` / your password), look at the Dashboard | An **Events** section with a panel "No Events Registered Yet". No table, no errors | TC-M01 |
| 2 | Close the app. Insert one sample row: `.\.venv\Scripts\python scripts\insert_test_event.py --create` then relaunch and sign in | A table with headers **Event ID, Event Name, Last Updated, Status** and one row: `1000`, `Star contender Doha`, a `DD/MM/YY HH:MM` time, badge **Registered**. Header says "1 registered" | TC-M02 |
| 3 | Check the time: the stored value is `2026-09-18T07:02:26Z` (UTC) | The column shows that instant in **your computer's local time**, 24-hour, e.g. UTC+3 → `18/09/26 10:02`, UTC+8 → `18/09/26 15:02` | TC-M03 |
| 4 | Close the app, run `scripts\insert_test_event.py --several`, relaunch, sign in | Five rows, newest first; badges **Registered / Ready (blue outline) / Synced (green) / Attention needed (red)**; "Event With No Update Yet" is last with an em dash (—) as its time; "5 registered" | TC-M04 |
| 5 | Add a row of your own with any SQLite viewer on `%TEMP%\ledsync-test\ledsync.db` (table `events`; `event_id` = `2000`, `event_name` = `My Test`, `last_updated` = `2026-09-08T16:45:00Z`). Relaunch | The row shows `08/09/26` and your local time (UTC+3 → `19:45`, matching the BRD's own example format) | TC-M05 |
| 6 | Edit that row's `last_updated` to `not a date`, then to `1969-12-31T23:59:59Z`, and relaunch each time | The **page still loads** and all other events still show; the odd value appears as its raw text | TC-M06 |
| 7 | Edit `status` to `Ready`, `synced`, `Attention needed`, then to `Something else` | Correct badge each time (case ignored); unknown text is shown in a grey badge, never dropped | TC-M07 |
| 8 | Use the keyboard: Tab through the Dashboard | Focus reaches Change Password, Sign Out, then the table area (visible focus ring) | TC-M08 |
| 9 | Clean up: close the app, `.\.venv\Scripts\python scripts\insert_test_event.py --clear`, or delete `%TEMP%\ledsync-test` | Only the sample rows are removed | TC-M09 |

Safety of the helper script (worth trying): run it with `LEDSYNC_DATA_DIR` unset, or pointed at `%LOCALAPPDATA%\LEDAssetSync`, or at a folder that doesn't exist — it must **refuse** each time.

## Summary

| Total cases | Passed | Failed | Blocked | Not yet run |
|---|---|---|---|---|
| 43 | 34 (31 automated TC-A01 – TC-A31, many parametrised, + 3 rendered-page TC-R01 – TC-R03) | 0 | 0 | 9 manual (TC-M01 – TC-M09, awaiting the user's run) |

`.\.venv\Scripts\python -m pytest -q` → **207 passed** (Phases 0–2 combined). The populated and empty states were also rendered from the app's real templates and viewed in Chrome (screenshots `docs/screenshots/phase2-dashboard-populated.jpg`, `phase2-dashboard-empty.jpg`).

## Test cases — automated (`tests/test_events.py`, plus regression in earlier suites)

| ID | Description | Expected result | Status | Notes |
|---|---|---|---|---|
| TC-A01 | Display format is `DD/MM/YY HH:MM`, 24-hour, zero-padded, in the chosen zone (8 cases incl. BRD's own `08/09/26 19:45`, fractional seconds, offsets, midnight rollover) | Exact strings | Pass | BRD 7.2 |
| TC-A02 | No AM/PM ever appears | 24-hour | Pass | |
| TC-A03 | Local-time display is correct for every accepted instant across 1970–2098 incl. winter/summer, agreeing with an independent computation | Match | Pass | Replaces a weaker test the architect review found tautological |
| TC-A04 | Empty / whitespace timestamp shows an em dash | `—` | Pass | |
| TC-A05 | Unparseable timestamp is shown raw, never hidden, never crashes | Raw text | Pass | |
| TC-A06 | Status normalisation: blank → Registered; case-insensitive Ready/Synced/Attention needed; unknown kept with neutral style | As expected (8 cases) | Pass | Owner status set |
| TC-A07 | Empty register → empty list; one row shows all four fields | As expected | Pass | Step 2.1 |
| TC-A08 | Newest first; undated and unparseable last; ties by Event ID | Order correct | Pass | |
| TC-A09 | Mixed timestamp formats (offset, `Z`, naive) sort by real time, not by text | A, B, C | Pass | |
| TC-A10 | Numeric event ID (hand-inserted) shown as text | `1000` | Pass | |
| TC-A11 | Dashboard with zero events: clean empty state, no table, no "None", no traceback | 200 | Pass | Step 2.1 |
| TC-A12 | Dashboard renders a manually inserted row incl. the timestamp format | `08/09/26 19:45`, headers, badge, "1 registered" | Pass | Step 2.1 validation |
| TC-A13 | All five badge classes render | Present | Pass | |
| TC-A14 | Event names are HTML-escaped (`<script>`, `<img onerror>`) | Escaped | Pass | *Security* |
| TC-A15 | Table has caption and `scope="col"` headers; "local time" note; region is keyboard-focusable with a label | Present | Pass | Accessibility |
| TC-A16 | Dashboard still requires login and the launch cookie | 302 / 403 | Pass | Regression |
| TC-A17 | A garbage row never breaks the page | 200 | Pass | |
| **TC-A18** | **20 hostile timestamps × 3 zones never raise** (before 1970, year 1, year 9999, overflow offsets, `2100`, NUL byte, leap second, `24:00`, NaN/inf, 100 000 characters, non-ASCII, ISO week) | Never raises | Pass | **Architect BLOCKER 1** — one such row previously gave HTTP 500 |
| **TC-A19** | **Dashboard returns 200 with each hostile value beside a good row; the good row is never hidden** | 200 (20 cases) | Pass | BLOCKER 1 |
| TC-A20 | Out-of-range dates (< 1970, > 2098) are shown raw; 1970-01-01 and 2098-12-31 are accepted | As expected | Pass | 2-digit year no longer ambiguous |
| TC-A21 | BLOB / numeric values in hand-edited rows are coerced to clean text (no `b'…'`) | Clean text | Pass | Review note 6 |
| TC-A22 | One unprocessable row is skipped and logged; the rest of the list still shows | Skipped | Pass | Defence in depth |
| TC-A23 | Rows keep raw `last_updated` and `status` for later phases | Present | Pass | Review note 4 |
| TC-A24 | An unexpected server error shows a branded page with the logo, no internals, no stock "Internal Server Error" | 500 branded | Pass | |
| TC-A25 | Dev script refuses when `LEDSYNC_DATA_DIR` is unset | Exit 2 | Pass | |
| TC-A26 | Dev script refuses the application's **real** data folder and creates nothing there | Exit 2 | Pass | Review finding 3 |
| TC-A27 | Dev script refuses a folder that doesn't exist unless `--create` (a typo must not create one) | Exit 2 / created | Pass | |
| TC-A28 | Dev script never overwrites an existing event with the same ID (skips and reports; row and its GUID/config untouched) | Untouched | Pass | Was `INSERT OR REPLACE` |
| TC-A29 | `--clear` removes only the script's own rows; `--several` covers every status | As expected | Pass | |
| TC-A30 | Text that is not valid UTF-8 in any column never hides the list (SQLite itself raises during fetch; now decoded leniently for this query only, connection setting restored) | 200, both rows shown | Pass | Re-check finding |
| TC-A31 | Year 2099 is shown raw (a zone ahead of UTC would otherwise display it as year "00"); accepted range is 1970–2098 | Raw text | Pass | Re-check note |

## Rendered-page checks (executed 21/09/26)

| ID | Description | Result |
|---|---|---|
| TC-R01 | Populated table (5 rows): full-width, strong borders, correct order, all four badge styles, em dash for undated | Pass |
| TC-R02 | Empty state panel with the WTT header/logo | Pass |
| TC-R03 | Times converted to this machine's local time (UTC+8: `2026-09-18T07:02:26Z` → `18/09/26 15:02`) | Pass |

## Open defects / follow-ups

| ID | Linked test case | Description | Severity | Status |
|---|---|---|---|---|
| F-13 | TC-M01 – M09 | Manual checks pending the user's run. | Info | Awaiting user |
| F-14 | TC-A03 | Windows applies its *current* DST rules to past/future dates when converting to local time, so a date well outside today's rules could be off by an hour. Inherent to local-time display; the on-screen note says times are local. | Info | Accepted |
| F-15 | — | Date-only stored values (`2026-09-08`) are treated as UTC midnight, so west of UTC they display as the previous day. Application-written values always carry a time, so this only affects hand-typed rows. | Low | Accepted |
| F-16 | TC-R01 | Automated real-window (native WebView2) login typing is unreliable on this machine (a Windows voice-typing overlay interferes with focus), so the populated table was verified via rendered pages in Chrome plus the owner's manual run rather than by keystroke automation of the native window. Browser password entry was deliberately not performed. | Info | Noted |
| F-12 | — | Earlier carry-forwards (single-instance lock, settings service excluding `admin_*`, log retention, migration runner, headless bootstrap, data-dir decision) unchanged. | — | Carried forward |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agents (two fresh contexts) | 21/09/26 | **First pass: Rejected** (BLOCKER: one odd timestamp → HTTP 500 on the whole dashboard; reproduced independently). Fixed: bounded date range, never-raise formatting, per-row isolation, branded 500 page, hardened dev script (TC-A18 – TC-A29). **Focused re-check: Approved with notes** — tried 46 hostile values × 3 zones, GET / end to end, and 12 ways around the dev-script guards (relative paths, case, 8.3 names, junctions); found one residual hole (invalid-UTF-8 TEXT), now fixed (TC-A30), plus the year-2099 display edge (TC-A31) |
| User (Vatsan) go-ahead | | | Pending |
