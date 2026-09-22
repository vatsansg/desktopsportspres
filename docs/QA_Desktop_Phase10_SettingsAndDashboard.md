# QA Test Case Document — Desktop Phase 10: Remaining Settings and Dashboard Polish

## Header

| Field | Value |
|---|---|
| Application | Desktop |
| Step / Phase | Phase 10 — Remaining Application Settings and Application Dashboard Polish (Step 10.1 the four remaining Settings screens, Step 10.2 the fuller Dashboard Actions column) |
| BRD reference(s) | Desktop BRD Section 14 (Download, Scheduling, Email, Application Settings), Section 18 (per-event timestamp cut-off), Section 24 (Dashboard Actions: Add Event, Download & Sync, Test Connections, View Logs, Settings) |
| Implementation Sequence reference | Steps 10.1, 10.2 |
| Date | 22/09/26 |
| Tested by | Claude Code (automated; **live against the real Azure account, read-only** for the per-event cut-off); user to run the manual guide below |
| Environment | Windows 11 Pro, Python 3.11.9, pywebview 6.2.1, WebView2 153.x; fake Azure for the automated tests; the real Event 1000 for the live cut-off check and the manual guide. |

## Owner decisions applied (22/09/26)

- **Application Settings locations are read-only** (owner): the database, application log and exception log locations are shown for reference only; moving a live database or log file safely is deferred to the installer/upgrade work (Phase 13).
- **The Last Updated Timestamp Cut-off is per event, not application-wide** (owner): set on the event's own Device Mapping page, stored on the `events` row (a new `cutoff_timestamp` column, added without a migration runner by an idempotent `ALTER TABLE`). Blank (the default) compares everything, as before Phase 10.
- **Scheduling and Email Settings screens are built now, storage only** (owner): the fields save and validate; a plain note on each screen says it has no effect yet. Scheduled runs arrive in Phase 12 and sending the notification in Phase 11.
- Defaults I chose (tell me if any is wrong): **Download retry count and delay are now configurable and shared by both download and push** ("Synchronisation behaviour" in the BRD is otherwise fixed and described on the Download Settings page); a **log retention** setting (carried from Phase 9, F-65) was added on the Application Settings screen and pruned at every application start; **Email Settings uses an Azure Communication Services connection string, not the BRD's literal SMTP server/port/username fields** — this is a deliberate deviation, because the confirmed Desktop BRD Addendum A §39.3 (20 Sep 2026) already says the email mechanism is Azure Communication Services, the same service the web application uses, so SMTP fields would only be built to be thrown away once Phase 11 arrives; the Dashboard's new **Test Connections** button reuses the existing Device Mapping "test all" action and lands on the Device Mapping page with the results, rather than staying on the Dashboard.

## How to test in the real app (for the user)

```powershell
cd C:\vatsan\techprojects\INFRA\ledassetmanagement\Applications\desktop
Remove-Item -Recurse -Force "$env:TEMP\ledsync-test-p10" -ErrorAction SilentlyContinue
$env:LEDSYNC_DATA_DIR = "$env:TEMP\ledsync-test-p10"
New-Item -ItemType Directory -Force $env:LEDSYNC_DATA_DIR | Out-Null
.\.venv\Scripts\python -m ledsync
```

| # | Do this | Expect | Covers |
|---|---|---|---|
| 1 | Sign in, open **Settings** | The section tabs now read Cloud Storage, Local Folders, **Download, Scheduling, Email, Application** | TC-M01 |
| 2 | Open **Download**. Type `2` for retries and `5` for the pause, save. Reload the page | "Download settings saved." The boxes still show `2` and `5`; the hint no longer says "default" | TC-M02 |
| 3 | Clear both boxes and save | "Download settings saved." The hint shows the built-in default again (retries `1`, pause `2.0`) | TC-M03 |
| 4 | Try `9` retries, or `-1`, or letters | Refused on screen with a plain message; nothing saved | TC-M04 |
| 5 | Open **Scheduling**. Tick **Enable scheduled runs** without choosing a day or time, save | Refused: "Choose at least one day and a time before enabling the schedule." | TC-M05 |
| 6 | Tick Mon/Wed/Fri, enter `02:30`, save | "Scheduling settings saved." Reload: the three boxes are still ticked and the time is still there | TC-M06 |
| 7 | Open **Email**. Tick **Send notification emails** without an address, save | Refused: needs a recipient | TC-M07 |
| 8 | Enter `it@example.com` and a connection string (any text 20+ characters), save. Reload the page | "Email settings saved." The connection string box is **empty** (never shown again); "Connection: Saved" | TC-M08 |
| 9 | Save again leaving the connection string box empty | The connection string stays as it was (only the recipient/enabled can be changed without retyping it) | TC-M09 |
| 10 | Open **Application** | Shows the real **Database location** and **Application log location** on this computer, "In the application database" for the exception log, and "Configured by the Windows installer". None of these have a box to edit | TC-M10 |
| 11 | Type `30` for **Keep logs for (days)**, save. Close the app and reopen it | "Application settings saved." Open **Logs**: rows older than 30 days would now be removed at each start (none will be old enough to see this on a fresh install — see TC-A for the automated proof) | TC-M11 |
| 12 | **ADD NEW EVENT → `1000` → REGISTER EVENT**. On the **Dashboard**, look at the event's row | The **Actions** column now has three buttons: **Download & Sync**, **Test Connections**, **View Logs** | TC-M12 |
| 13 | Map at least one LED to a real or scratch folder on the event's Device Mapping page, then go back to the **Dashboard** and click **Test Connections** | You land on the Device Mapping page with the test result shown (OK/Failed per destination) | TC-M13 |
| 14 | On the Dashboard, click **View Logs** for the event | The Logs page opens already filtered to that event | TC-M14 |
| 15 | Open the event's **Device Mapping** page. Find the new **Timestamp Cut-off** section, enter a date and time (UTC), save | "Timestamp cut-off saved." The box keeps the value on reload | TC-M15 |
| 16 | Set the cut-off to a date **after** today (UTC), then **Change Log → CHECK FOR CHANGES** | A new chip **"Skipped (Before Cut-Off)"** appears with a count, and an expandable section lists which files and why. Nothing from before the cut-off is offered for download | TC-M16 |
| 17 | Clear the cut-off (leave the box empty) and save, then check again | The change log behaves exactly as before Phase 10 — nothing is skipped by the cut-off | TC-M17 |
| 18 | Type `not a date` into the cut-off box and save | Refused with a plain message; nothing saved | TC-M18 |
| 19 | Keyboard only: Tab through Scheduling (checkboxes, day picker, time) and the cut-off form | Visible focus ring throughout; the day checkboxes and the enable checkbox are reachable and toggle with Space | TC-M19 |

Clean up: close the app, then `Remove-Item -Recurse -Force "$env:TEMP\ledsync-test-p10"`.

Live check (read-only, re-runnable): `.\.venv\Scripts\python -m pytest -q --live tests/test_live_azure.py -k phase10`.

## Summary

| Total cases | Passed | Failed | Blocked | Not yet run |
|---|---|---|---|---|
| 34 | 15 (automated groups covering 29 new pytest cases, 1 live check against real Azure, 1 rendered-page group — all run by Claude) | 0 | 0 | 19 (manual TC-M01 – TC-M19, for the owner) |

`.\.venv\Scripts\python -m pytest -q` → **1435 passed, 14 skipped** (the skipped are the live-Azure tests, which need `--live`).

## Live validation against the real Azure account (read-only, 22/09/26)

| ID | Check | Result |
|---|---|---|
| TC-L01 | A cut-off set in the future on the real Event 1000 skips its real changes (the "Skipped" chip appears); clearing it restores the normal check (the usual "New, Not In Log" chip returns) | Pass |

## Test cases — automated

| ID | Description | Status |
|---|---|---|
| TC-A01 | **Download settings:** default to the `transfer` module's own constants; save/validate/blank-means-default; range and type errors refused; audited | Pass |
| TC-A02 | **Log retention:** default forever; save/validate a day count; blank clears it back to forever; range errors refused | Pass |
| TC-A03 | **Scheduling settings:** enabling without a day or time is refused; forged/duplicate/unknown day values are dropped and stored in week order, not input order; time format validated; no-change save reported correctly | Pass |
| TC-A04 | **Email settings:** enabling without a recipient is refused; recipient validated; a key-shaped value in the recipient box is refused; the connection string is never echoed back; a blank connection string on save keeps the existing one; a too-short "connection string" is refused | Pass |
| TC-A05 | **Retry generalisation:** a transient failure is retried up to the configured count then gives up; zero retries means the first failure is final; the push engine (`sync.py`) shares the same count/delay as the download engine | Pass |
| TC-A06 | **Settings pages:** need login and the launch cookie; save/error paths for all four new pages; a refused save is logged as an Invalid configuration exception with no typed value; the Settings navigation lists all six sections | Pass |
| TC-A07 | **Per-event cut-off (service layer):** default blank compares everything; save validates, normalises to UTC, is strictly per event (a second event is untouched); an unregistered event is refused; the decision logic skips only new work (Download/Delete) before the cut-off and leaves already-processed files exactly as they were, for both New/Updated and Deleted entries; the cut-off also applies to Azure-only "not in the log" files | Pass |
| TC-A08 | **Per-event cut-off (web):** the form on the Device Mapping page saves, shows the saved value, validates, needs CSRF and login, 404s an unregistered event; `check_event` uses the saved cut-off and reports the skipped count in the operational log | Pass |
| TC-A09 | **Startup log retention:** rows older than the setting are removed at startup (both logs), a summary row is written only when something was removed; "forever" (the default) prunes nothing | Pass |
| TC-A10 | **Dashboard Actions:** the row offers Test Connections (posts to the existing device-mapping test-all action) and View Logs (links to the Logs page pre-filtered to that event) | Pass |
| TC-A11 | **Schema:** the new `events.cutoff_timestamp` column is added correctly for a brand-new database (in the DDL) and for a database created before Phase 10 (idempotent `ALTER TABLE`, appended in the same position so the column-order check still passes; run twice with no error; existing data untouched) | Pass |
| TC-A12 | Earlier phases unchanged (the brand rule test caught an accent colour used outside the primary button - fixed; all earlier tests pass) | Pass |

## Rendered-page checks

| ID | Description | Result |
|---|---|---|
| TC-R01 | Dashboard with the three-button Actions column, the event's Device Mapping page with the Timestamp Cut-off section, and Settings → Scheduling with the day picker (`docs/screenshots/phase10-*.png`). One orange action per screen; bordered controls; readable on black | Pass |

## Open defects / follow-ups

| ID | Description | Severity | Status |
|---|---|---|---|
| F-68 | Scheduling and Email settings have no effect yet (by design; Phases 12 and 11 use them). | Info | Carried to Phases 11 / 12 |
| F-69 | The Application Settings locations (database, log files) cannot be changed from the page (owner decision); moving them safely needs the installer/upgrade work. | Info | Carried to Phase 13 |
| F-70 | The retry count/delay setting is a single, application-wide value shared by every event and every device; there is no per-event or per-device override. | Low | Accepted |
| F-63/F-64/F-65/F-66 | Carried from Phase 9 (scheduled/update log events, no free-text resolution note, log rows kept per the new retention setting, large-log performance not measured directly). | — | Carried forward |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context) | pending | pending |
| User (Vatsan) go-ahead | Vatsan | pending | pending |
