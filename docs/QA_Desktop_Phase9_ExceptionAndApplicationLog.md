# QA Test Case Document — Desktop Phase 9: Exception Log and Application Log

## Header

| Field | Value |
|---|---|
| Application | Desktop |
| Step / Phase | Phase 9 — Error Handling, Exception Log, Application Log (Step 9.1 exception log with resolution and review, Step 9.2 operational log with run-level entries, and the **Logs** page that shows both) |
| BRD reference(s) | Desktop BRD Sections 21 (error categories), 21.1 (exception log fields; the administrator can review errors after an operation), 24 (Dashboard action "View Logs"), 25 (operational log, DD/MM/YY HH:MM:SS) |
| Implementation Sequence reference | Steps 9.1, 9.2 |
| Date | 22/09/26 |
| Tested by | Claude Code (automated); user to run the manual guide below |
| Environment | Windows 11 Pro, Python 3.11.9, pywebview 6.2.1, WebView2 153.x; fake Azure for the automated tests; the real Event 1000 for the manual guide. |

## Owner decisions applied (22/09/26)

- **One Logs page, two tabs** (owner): **View Logs** in the top bar of every signed-in page opens **Logs**, with an **Operational Log** tab and an **Exception Log** tab, newest first, filterable by event, operation / error category, status / resolution, date range (the local day) and text.
- **Resolution / status** (owner): every exception starts **Open**. The administrator can set **Open**, **Acknowledged** or **Resolved** on any row (audited in the operational log as *Exception Reviewed*). A **later success of the same thing** marks it **Resolved** automatically — same event, operation, table, LED type, file, source and destination (a failed device test is resolved by a passing test of that device, a failed push by the same file reaching the same device, a failed download by the same file downloading, a refused registration by the event registering, a failed check by a successful check, a failed cloud test by a successful one).
- **Retention** (owner): everything is kept; nothing is deleted automatically. A retention setting can come with Phase 10.
- **Export** (owner): **Export CSV** downloads exactly what the current filter shows (up to 50,000 rows), with the BRD column names, spreadsheet-safe.
- Defaults I chose (tell me if any is wrong): times are shown in this computer's local time (stored in UTC); the resolution **note** mentioned when the options were listed is **not** included — changing the status is the review (no schema change needed); a refused save of settings or a device mapping is now recorded as an **Invalid configuration** exception plus a *Failed* operational-log row (typed values in quotes are never stored, so a key pasted into the wrong box cannot reach the log); every **Download & Sync**, **Download Files** and **Sync Files** run now writes a *Started* row and a finished row (*Success*, *Failed* or *Cancelled*, with the counts); **Scheduled Run** and **Application Update** appear in the Operation filter but nothing writes them until Phases 12 and 13.

## How to test in the real app (for the user)

Use PowerShell (the commands below are PowerShell). Real Event 1000 in Azure (read-only).

```powershell
cd C:\vatsan\techprojects\INFRA\ledassetmanagement\Applications\desktop
Remove-Item -Recurse -Force "$env:TEMP\ledsync-test-p9","$env:TEMP\ledsync-devices" -ErrorAction SilentlyContinue
$env:LEDSYNC_DATA_DIR = "$env:TEMP\ledsync-test-p9"
New-Item -ItemType Directory -Force $env:LEDSYNC_DATA_DIR | Out-Null
foreach ($d in "t1i","t1o","t1m","t2i") { New-Item -ItemType Directory -Force "$env:TEMP\ledsync-devices\$d" | Out-Null }
.\.venv\Scripts\python -m ledsync
```

| # | Do this | Expect | Covers |
|---|---|---|---|
| 1 | Sign in. Look at the top bar on the Dashboard | A new **View Logs** button between the user name and **Settings** (also on every other signed-in page) | TC-M01 |
| 2 | Click **View Logs** | **Logs** opens on the **Operational Log**: an *Application Startup* row and a *Login* row, times like `22/09/26 08:47:02` (DD/MM/YY HH:MM:SS, your local time — compare with your clock). One orange button only (**APPLY FILTERS**) | TC-M02 |
| 3 | **ADD NEW EVENT → `1000` → REGISTER EVENT**; open the event and map the four scratch folders (`%TEMP%\ledsync-devices\t1i`, `t1o`, `t1m`, `t2i` for Table 1 Inner / Outer / Main LED and Table 2 Inner); **SAVE AND TEST ALL**; then Dashboard → **Download & Sync** and wait until it finishes | The event is registered, tested and synced | TC-M03 |
| 4 | Open **View Logs** again | The full cycle is there, newest first: *Login*, *Event Registered*, *Mapping Saved*, *Device Test* (four), *Download & Sync — Started*, *Check Changes*, *RPI Files* and *Asset Download* rows (one per file), *Synchronise* rows (one per file pushed), then *Download & Sync — Success* with "Identified …, downloaded 76, synchronised 75, errors 0." Times increase in order; no row shows a key or password | TC-M04 |
| 5 | Filter: **Operation = Synchronise**, then **Status = Failed**, then **From/To = today**, then type `Table 1 Outer` in **Contains**. Press **APPLY FILTERS** each time. **Clear** resets | The list and the count ("N records") follow each filter. A future date range shows "Nothing To Show" | TC-M05 |
| 6 | **Errors, part 1 — unreachable device.** In PowerShell rename `%TEMP%\ledsync-devices\t1o` to `t1o-gone`. In the app open the event → **Device Mapping → Test** for Table 1 Outer | The row shows Connection Failed. In **Logs → Exception Log** a new **Open** row: Event 1000, Table 1, Outer, Operation *Test Connection*, Category *Missing folder* (or *Network/device connectivity*), a plain description, the folder as Destination | TC-M06 |
| 7 | **Errors, part 2 — refused settings.** On the Device Mapping page type `not a path` in a folder box and save; then **Settings → Local Folders**, type `relative\x` in the RPI box and save | Both are refused on screen. The Exception Log gets two more **Open** rows, category *Invalid configuration*, operations *Save Device Mapping* and *Save Folder Settings*; the text you typed is **not** in the log (a `'...'` stands in) | TC-M07 |
| 8 | **Errors, part 3 — no such event.** **ADD NEW EVENT → `9999` → REGISTER EVENT** | "not found" style message on screen; the Exception Log has a *Register Event* row with the storage category | TC-M08 |
| 9 | **Automatic resolution.** Rename `t1o-gone` back to `t1o` and press **Test** for Table 1 Outer again | The Table 1 Outer *Test Connection* exception now reads **Resolved** by itself (the other rows stay Open) | TC-M09 |
| 10 | **Review.** On any Open row choose **Acknowledged** in its list and press **Save**; then choose **Resolved** on another | The page reloads on the Exception Log with the same filters and "Exception N marked Acknowledged." The badge changes colour. In the Operational Log there is an *Exception Reviewed* row for each change | TC-M10 |
| 11 | **Filter by resolution:** Exception Log → **Resolution = Open**, then **Error Category = Invalid configuration** | Only matching rows | TC-M11 |
| 12 | **Failed sync, then success.** Close the app and repeat the setup lines for a fresh data folder, register `1000`, map the four folders, then rename `%TEMP%\ledsync-devices\t1o` to `t1o-gone` **before** clicking **Download & Sync**. When it finishes open the Exception Log, then rename the folder back and click **Download & Sync** again | After the first run: Open *Synchronise* exceptions for Table 1 Outer, and the run row in the Operational Log says *Failed* with "errors 25". After the second run the run row says *Success* and those exceptions read **Resolved** by themselves | TC-M12 |
| 13 | **Export.** On the Exception Log with a filter set press **Export CSV**; then the same on the Operational Log | A native **Save As** dialog opens, starting in your Downloads folder, named `ledsync_exception_log.csv` (then `ledsync_operational_log.csv`). Save it, then open in Excel: exception columns are Date/Time, Event ID, Table, LED Type, File Name, Operation, Error Category, Error Description, Source, Destination, Resolution/Status; only the filtered rows are in; times are `DD/MM/YY HH:MM:SS`. The Operational Log now has a *Log Export* row | TC-M13 |
| 14 | Keyboard only: Tab through the Logs page (tabs, filters, table, review list and Save) | Visible focus ring everywhere; each review list and Save button announces which exception it changes | TC-M14 |
| 15 | Close the app and start it again (same `LEDSYNC_DATA_DIR`), sign in, open the Logs | Everything from before is still there (nothing is deleted); a second *Application Startup* row is added | TC-M15 |

Clean up: close the app, then `Remove-Item -Recurse -Force "$env:TEMP\ledsync-test-p9","$env:TEMP\ledsync-devices"`.

## Summary

| Total cases | Passed | Failed | Blocked | Not yet run |
|---|---|---|---|---|
| 32 | 32 (automated groups covering 30 new pytest cases plus all earlier tests, and 1 rendered-page group — all run by Claude; 15 manual TC-M01 – TC-M15 run by the owner on 22/09/26, including the export defect found and fixed during testing: "All good") | 0 | 0 | 0 |

`.\.venv\Scripts\python -m pytest -q` → **1405 passed, 13 skipped** (the skipped are the live-Azure tests, which need `--live`).

## Test cases — automated

| ID | Description | Status |
|---|---|---|
| TC-A01 | **Categories:** all ten BRD Section 21 categories can be logged; an unknown one is refused | Pass |
| TC-A02 | **Auto-resolution:** matches exactly the same event / operation / table / LED / file / destination (case-insensitive) and nothing else; already-resolved rows untouched; an Acknowledged row is still resolved; a field that is not given must be empty in the row; a failed device test is resolved by a passing one; a failed push by the same file reaching the same device (a second file's failure stays Open); a registration refusal by the event registering | Pass |
| TC-A03 | **Review:** set a status, unknown id, invalid status refused, audited as *Exception Reviewed* | Pass |
| TC-A04 | **Refused configuration:** a refused device mapping and a refused folder setting are *Invalid configuration* exceptions plus *Failed* operational rows; a pasted key-like value is never stored | Pass |
| TC-A05 | **Step 9.1 validation:** a wrong GUID, an unreachable device folder, a refused configuration and an event that is not in Azure are each categorised correctly and appear on the Exception Log page | Pass |
| TC-A06 | **Querying:** filters by event, category, status, date range and text (`%` and `_` are literal), newest first, paging and totals — for both logs | Pass |
| TC-A07 | **The Logs page:** login and launch cookie required; times as DD/MM/YY HH:MM:SS in the display time zone (also across the new year); newest first; output escaped; 230 rows → 3 pages with Older/Newer; date filter means the local day (exclusive end); bad page/date/tab input never breaks the page | Pass |
| TC-A08 | **Review from the page:** CSRF required, invalid status 400, unknown id 404, redirect keeps the filter, accessible names on each control, one primary action | Pass |
| TC-A09 | **Export:** filtered rows only, BRD headers, byte-order mark, formula cells neutralised, `no-store`, attachment name, audited | Pass |
| TC-A10 | **Step 9.2 validation — full login-to-sync cycle:** startup, login, event registered, mapping saved, device test, settings changed, cloud test, check changes, run started, asset download, RPI files, synchronise, run finished, failed password change and logout are all in the operational log, in order, every timestamp within the test's time window, no password in any row | Pass |
| TC-A11 | **Run rows:** a failed run is recorded *Failed* with its counts; a push-only and a download-only run have their own names; the Scheduled Run and Application Update names are reserved in the filter | Pass |
| TC-A12 | **Independent-review regression tests (3):** a typed network host in a refused mapping is quoted before logging (never stored raw); an astronomically large page number never crashes the Logs page; a huge or negative offset is clamped rather than raising | Pass |
| TC-A13 | Earlier phases unchanged (top bar gains one link; all earlier tests pass) | Pass |
| TC-A14 | **Owner-found defect:** downloads are allowed before the window opens, so the CSV export actually reaches disk | Pass |

## Rendered-page checks

| ID | Description | Result |
|---|---|---|
| TC-R01 | Logs — Operational Log and Exception Log with filters, badges, review controls (`docs/screenshots/phase9-logs-*.png`). One orange action; bordered controls; readable on black | Pass |

## Open defects / follow-ups

| ID | Description | Severity | Status |
|---|---|---|---|
| F-67 | **Owner-found (22/09/26): the CSV export produced no file.** `pywebview` refuses every download by default (WebView2 cancels it silently, with no error and no visible message) unless the host application explicitly allows it. | Medium | **Closed** — `webview.settings["ALLOW_DOWNLOADS"] = True` is now set before the window opens; WebView2 then shows its own native **Save As** dialog (defaulting to the Downloads folder), which also serves as the operator's confirmation that the export happened and where it went. A regression test starts the real startup path with a faked `webview` module and asserts the setting is on before the window is created. |
| F-66 | Query performance on a large log (100,000+ rows) was not measured directly; three indexes were added (`timestamp`, `event_id` on each log, plus `resolution_status` on the exception log) as a precaution, since the schema has no migration runner and `CREATE INDEX IF NOT EXISTS` is safe to add to the same idempotent script. | Low | Accepted |
| F-63 | **Scheduled executions** and **application updates** (BRD Section 25) cannot be logged yet — the features arrive in Phases 12 and 13; their names are reserved. | Info | Carried to Phases 12 / 13 |
| F-64 | The exception status has no free-text note (a note needs a schema change; no migration runner yet). | Low | Accepted |
| F-65 | Nothing is ever deleted from the logs (owner decision); a retention setting is expected with Phase 10. | Info | Carried to Phase 10 |
| F-12 | Earlier carry-forwards (single-instance lock, migration runner) unchanged. | — | Carried forward |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Owner-reported defect (22/09/26) | Vatsan | 22/09/26 | "not seeing the export file … need a pop up message to confirm the file exported" — root cause found (pywebview downloads disabled by default) and fixed same day; see F-67 |
| Independent Solution Architect review | Independent review agent (fresh context) | 22/09/26 | **Approved with notes after fixes** — two "fix now" findings, both reproduced and fixed: a refused UNC device folder with no share name embedded the typed host **unquoted** in the exception and operational logs (now quoted like every sibling validation message, so the rejection scrub removes it); an astronomically large `page` value in the URL caused an unhandled `OverflowError` (500) on the Logs page (page and offset are now clamped). Also added on the reviewer's note: indexes on the log tables' `timestamp`, `event_id` and (exception log) `resolution_status` columns, ahead of real volume. 3 regression tests; **not re-reviewed**. Verified sound: auto-resolution scoping (never cross-event or cross-file), transaction safety, run-row accounting in every path, LIKE escaping, CSRF and the review redirect's whitelist, CSV formula neutralisation, date-filter exception handling. |
| User (Vatsan) go-ahead | Vatsan | 22/09/26 | Approved — "All good move to completing this step, commit and push to github and move to next step" |
