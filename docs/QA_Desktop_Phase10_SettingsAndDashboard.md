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
| Environment | Windows 11 Pro, Python 3.11.9, pywebview 6.2.1, WebView2 153.x; fake Azure for the automated tests; the real Event 1000 for the live check and the manual guide. |

## Owner decisions applied (22/09/26)

- **Application Settings locations are read-only** (owner): the database, application log and exception log locations are shown for reference only; moving a live database or log file safely is deferred to the installer/upgrade work (Phase 13).
- **The Timestamp Cut-off is a recurring DAILY quiet period, per event** (owner, corrected 22/09/26 after reviewing the first build — see "Owner-directed redesign" below): a time of day (UTC), not a one-time date, with its own **Enable cut-off** switch as the emergency override. Off by default, compares everything, exactly as before Phase 10 existed.
- **Scheduling and Email Settings screens are built now, storage only** (owner): the fields save and validate; a plain note on each screen says it has no effect yet. Scheduled runs arrive in Phase 12 and sending the notification in Phase 11.
- **A deletion held by the cut-off keeps the local copy until the next window, same as a late upload** (owner, 22/09/26).
- **The cut-off time is typed in UTC, with this computer's local-time equivalent shown live alongside it** (owner, 22/09/26).
- Defaults I chose (tell me if any is wrong): **Download retry count and delay are now configurable and shared by both download and push** ("Synchronisation behaviour" in the BRD is otherwise fixed and described on the Download Settings page); a **log retention** setting (carried from Phase 9, F-65) was added on the Application Settings screen and pruned at every application start; **Email Settings uses an Azure Communication Services connection string, not the BRD's literal SMTP server/port/username fields** — this is a deliberate deviation, because the confirmed Desktop BRD Addendum A §39.3 (20 Sep 2026) already says the email mechanism is Azure Communication Services, the same service the web application uses, so SMTP fields would only be built to be thrown away once Phase 11 arrives; the Dashboard's new **Test Connections** button reuses the existing Device Mapping "test all" action and lands on the Device Mapping page with the results, rather than staying on the Dashboard.

## Owner-directed redesign of the Timestamp Cut-off (22/09/26)

The cut-off was first built as a one-time absolute date — anything before it skipped forever, everything after it downloaded normally. The owner corrected this after reviewing it: it needed to be a **recurring daily quiet period before showtime**, so a last-minute change never reaches the LED devices unreviewed. It was rebuilt to that specification before any manual testing began:

- **Enable cut-off** — a separate on/off switch. Off is the emergency override: everything downloads immediately, cut-off ignored.
- **Cut-off time** — a time of day (UTC, 24-hour), not a date. It repeats every day.
- Every check computes the **boundary**: the most recent time the cut-off has already passed — today's occurrence if it has happened today, otherwise yesterday's.
- A change timestamped **at or before** the boundary downloads or is removed normally. A change timestamped **after** the boundary is **held**, not lost: it is reconsidered fresh on every later check and is downloaded or removed automatically once the *next* day's occurrence of the cut-off passes it.
- Worked example (owner's own numbers): cut-off `21:00` UTC, last successful sync `06:00` UTC yesterday, a file changed `22:00` UTC yesterday (one hour after yesterday's `21:00`). Before today's `21:00` has happened, the boundary is still yesterday's `21:00`, so a run now downloads everything from `06:00` to `21:00` yesterday — but **not** the `22:00` file. Once today's `21:00` passes, the boundary moves to today's `21:00`, and the `22:00`-yesterday file (now before the new boundary) is downloaded on the next check.

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
| 15 | Open the event's **Device Mapping** page. Find the new **Timestamp Cut-off** section | A live line reads "This computer's current UTC time is …", updating every 30 seconds | TC-M15 |
| 16 | Tick **Enable cut-off** and save without a time | Refused: "Enter a cut-off time before turning it on." | TC-M16 |
| 17 | Enter `21:00` in the Cut-off time box (do **not** tick Enable yet) | A live line under the box reads "= H:MM local time today", matching what 21:00 UTC is on this computer right now | TC-M17 |
| 18 | Tick **Enable cut-off**, keep `21:00`, save | "Timestamp cut-off saved." The checkbox and time are both still set on reload | TC-M18 |
| 19 | **Change Log → CHECK FOR CHANGES** | If any real change is timestamped after the most recent `21:00` UTC boundary, a chip **"Held (After Cut-Off)"** appears with a count and an expandable section listing which files and why; everything at or before the boundary downloads normally | TC-M19 |
| 20 | Untick **Enable cut-off** (leave the time as it is) and save, then check again | "Timestamp cut-off saved." The change log behaves exactly as before Phase 10 — nothing is held, regardless of timestamp (the emergency override) | TC-M20 |
| 21 | Type `9am` or `25:00` into the cut-off time box and save | Refused with a plain message; nothing saved | TC-M21 |
| 22 | Keyboard only: Tab through Scheduling (checkboxes, day picker, time) and the cut-off form | Visible focus ring throughout; the day checkboxes and the enable checkbox are reachable and toggle with Space | TC-M22 |

Clean up: close the app, then `Remove-Item -Recurse -Force "$env:TEMP\ledsync-test-p10"`.

Live check (read-only, re-runnable): `.\.venv\Scripts\python -m pytest -q --live tests/test_live_azure.py -k phase10`.

## Summary

| Total cases | Passed | Failed | Blocked | Not yet run |
|---|---|---|---|---|
| 38 | 16 (automated groups covering 40 new pytest cases, 1 live check against real Azure, 1 rendered-page group — all run by Claude) | 0 | 0 | 22 (manual TC-M01 – TC-M22, for the owner) |

`.\.venv\Scripts\python -m pytest -q` → **1441 passed, 14 skipped** (the skipped are the live-Azure tests, which need `--live`).

## Live validation against the real Azure account (read-only, 22/09/26)

| ID | Check | Result |
|---|---|---|
| TC-L01 | The recurring cut-off saves, enables and disables correctly against the real Event 1000 and the real page, and a normal check still completes with it on or off. (Whether a real file is held depends on how recently the web team touched it relative to "now" — during this run, one real file genuinely was held, correctly, because it had been changed after the computed boundary; the controlled hold/release mechanics are proven with a fixed clock in the automated suite, not against live, changing data.) | Pass |

## Test cases — automated

| ID | Description | Status |
|---|---|---|
| TC-A01 | **Download settings:** default to the `transfer` module's own constants; save/validate/blank-means-default; range and type errors refused; audited | Pass |
| TC-A02 | **Log retention:** default forever; save/validate a day count; blank clears it back to forever; range errors refused | Pass |
| TC-A03 | **Scheduling settings:** enabling without a day or time is refused; forged/duplicate/unknown day values are dropped and stored in week order, not input order; time format validated; no-change save reported correctly | Pass |
| TC-A04 | **Email settings:** enabling without a recipient is refused; recipient validated; a key-shaped value in the recipient box is refused; the connection string is never echoed back; a blank connection string on save keeps the existing one; a too-short "connection string" is refused | Pass |
| TC-A05 | **Retry generalisation:** a transient failure is retried up to the configured count then gives up; zero retries means the first failure is final; the push engine (`sync.py`) shares the same count/delay as the download engine | Pass |
| TC-A06 | **Settings pages:** need login and the launch cookie; save/error paths for all four new pages; a refused save is logged as an Invalid configuration exception with no typed value; the Settings navigation lists all six sections | Pass |
| TC-A07 | **Recurring cut-off (service layer):** default off compares everything; enabling without a time is refused; save is strictly per event (a second event untouched); an unregistered event is refused; `cutoff_boundary` computes the most recent past occurrence correctly (today's if already passed, yesterday's if not, exactly-on-the-boundary is inclusive, off/no-time gives no boundary); the decision logic holds only new work (Download/Delete) after the boundary and leaves already-processed files exactly as they were; a held file is downloaded once a later boundary passes it; the boundary also applies to Azure-only "not in the log" files; a deletion after the boundary keeps the local copy for now | Pass |
| TC-A08 | **Recurring cut-off (web):** the form on the Device Mapping page saves both the switch and the time, shows the saved values, validates the time format, refuses enabling without a time, needs CSRF and login, 404s an unregistered event, keeps the time when disabling (no need to retype it); `check_event` uses the saved cut-off and reports the held count in the operational log | Pass |
| TC-A09 | **Startup log retention:** rows older than the setting are removed at startup (both logs), a summary row is written only when something was removed; "forever" (the default) prunes nothing | Pass |
| TC-A10 | **Dashboard Actions:** the row offers Test Connections (posts to the existing device-mapping test-all action) and View Logs (links to the Logs page pre-filtered to that event) | Pass |
| TC-A11 | **Schema:** the new `events.cutoff_enabled` / `events.cutoff_time` columns are added correctly for a brand-new database (in the DDL) and for a database created before Phase 10 (idempotent `ALTER TABLE`, appended in the same position so the column-order check still passes; run twice with no error; existing data untouched) | Pass |
| TC-A12 | **Independent-review regression tests (4):** two concurrent runs with different retry settings never see or leave behind each other's value, proven on real threads; `_run_job` passes the saved retry setting down as a plain argument and never touches the `transfer` module's constants; a malformed cut-off time gets a clear message; the cut-off field's live UTC clock and local-time preview are present on the page | Pass |
| TC-A13 | Earlier phases unchanged (the brand rule test caught an accent colour used outside the primary button - fixed; all earlier tests pass) | Pass |

## Rendered-page checks

| ID | Description | Result |
|---|---|---|
| TC-R01 | Dashboard with the three-button Actions column, the event's Device Mapping page with the recurring Timestamp Cut-off section (switch, time, live local-time preview, live UTC clock), and Settings → Scheduling with the day picker (`docs/screenshots/phase10-*.png`). One orange action per screen; bordered controls; readable on black | Pass |

## Open defects / follow-ups

| ID | Description | Severity | Status |
|---|---|---|---|
| F-68 | Scheduling and Email settings have no effect yet (by design; Phases 12 and 11 use them). | Info | Carried to Phases 11 / 12 |
| F-69 | The Application Settings locations (database, log files) cannot be changed from the page (owner decision); moving them safely needs the installer/upgrade work. | Info | Carried to Phase 13 |
| F-70 | The retry count/delay setting is a single, application-wide value shared by every event and every device; there is no per-event or per-device override. | Low | Accepted |
| F-71 | The local-time preview next to the cut-off time box shows today's conversion; across a DST change the same UTC time can show a different local hour on either side of the change. The box itself is always UTC-authoritative, so this is a display nuance, not a stored-value problem. | Info | Accepted |
| F-63/F-64/F-65/F-66 | Carried from Phase 9 (scheduled/update log events, no free-text resolution note, log rows kept per the new retention setting, large-log performance not measured directly). | — | Carried forward |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Owner review of the first build | Vatsan | 22/09/26 | **Corrected before testing** — the Timestamp Cut-off was a one-time absolute date; the owner specified a recurring daily quiet period instead (see "Owner-directed redesign" above) and asked for confirmation before implementation, which was given via clarifying questions before any code was rewritten. |
| Independent Solution Architect review (first build, retry/settings scope) | Independent review agent (fresh context) | 22/09/26 | **Approved with notes after a fix** — one "fix now" finding, reproduced and fixed: the shared retry-count/delay setting was applied by temporarily mutating the `transfer` module's constants around each background job; two jobs for different events running at the same time could observe or permanently leave behind each other's value. Fixed by removing the mutable global — the saved setting is now loaded once per job and passed down as an ordinary argument through every engine. Also fixed on the reviewer's note: the cut-off field's UTC framing relied on label text alone, now backed by a live UTC clock. This review covered the Settings pages, Dashboard, schema and retry mechanism; it predates the cut-off redesign below and did not review the recurring cut-off logic. |
| Independent Solution Architect review (recurring cut-off redesign) | pending | pending | pending |
| User (Vatsan) go-ahead | Vatsan | pending | pending |
