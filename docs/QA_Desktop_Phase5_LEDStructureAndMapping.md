# QA Test Case Document — Desktop Phase 5: LED Structure & Device Mapping

## Header

| Field | Value |
|---|---|
| Application | Desktop |
| Step / Phase | Phase 5 — LED Structure & Device Mapping (Step 5.1 LED structure, Step 5.2 device / folder mapping, Step 5.3 connection testing) |
| BRD reference(s) | Desktop BRD Sections 11 (LED structure), 12 (device mapping), 13 (connection testing), 20 (shared-folder path example), 21 (error categories), 33 (security), 34 (business rules) |
| Implementation Sequence reference | Steps 5.1, 5.2, 5.3 |
| Date | 21/09/26 |
| Tested by | Claude Code (automated; rendered pages; real folders, real Windows error codes); user to run the manual guide below |
| Environment | Windows 11 Pro, Python 3.11.9, pywebview 6.2.1, WebView2 153.x; scratch local database; fake Azure for registration in automated tests (the real account is not touched by this phase). |

## Owner decisions applied (21/09/26)

- **Test = folder access** — no ping. The device is judged by whether its shared folder can be opened.
- **Write access is proven** by creating a tiny, uniquely named, empty file (`.ledsync-probe-<random>.tmp`) and deleting it at once. It never overwrites (exclusive create) and never touches an existing file.
- **LED types** are `Inner`, `Outer`, `MainLED` (shown as "Main LED"), matched case-insensitively (the real cloud folders are lower-case).
- **Re-registration:** mappings that still match the new configuration are kept (status reset to *Not tested*); LEDs that were removed are hidden (not deleted, listed under "Previously mapped"); new LEDs appear unmapped.
- **No shared-folder credentials are stored or asked for.** The app uses the access of the Windows account it runs under. *(Venue authentication method is still an open BRD §36 item — see follow-ups.)*
- **IP address is optional** (validated when given). The shared folder is what the test uses.
- Folder must be a UNC path `\\host\share[\sub]` or an absolute local path `X:\folder` (for simulated devices); **duplicates** across destinations of one event are rejected; the Windows, Program Files and ProgramData folders and this application's own data folder are refused.
- Each test is time-limited (10 s; **Test All** runs the destinations in parallel under one shared limit), so a switched-off device never freezes the application.

## How to test in the real app (for the user)

Everything below uses folders on your own machine, so no venue hardware is needed. Step 12 repeats the checks with your real shared folders.

```powershell
cd C:\vatsan\techprojects\INFRA\ledassetmanagement\Applications\desktop
Remove-Item -Recurse -Force "$env:TEMP\ledsync-test-p5","$env:TEMP\ledsync-shares" -ErrorAction SilentlyContinue
$root = "$env:TEMP\ledsync-shares"
"T1-Inner","T1-Outer","T1-Main","T2-Inner" | ForEach-Object { New-Item -ItemType Directory -Force "$root\$_" | Out-Null }
$env:LEDSYNC_DATA_DIR = "$env:TEMP\ledsync-test-p5"
New-Item -ItemType Directory -Force $env:LEDSYNC_DATA_DIR | Out-Null
.\.venv\Scripts\python -m ledsync
```
Sign in (`admin` / your password). The key is read from your git-ignored `.env`, so **ADD NEW EVENT → `1000` → REGISTER EVENT** works straight away. The folders you will map are `%TEMP%\ledsync-shares\T1-Inner`, `T1-Outer`, `T1-Main` and `T2-Inner`.

| # | Do this | Expect | Covers |
|---|---|---|---|
| 1 | Register event `1000`. On the Dashboard click the **event ID or the name** | The **Event Details** page opens: "← Dashboard", the event name, "Event 1000 · Registered" | TC-M01 |
| 2 | Read **LED Configuration** | A table: Table 1 → Inner ✓ Enabled, Outer ✓ Enabled, Main LED ✓ Enabled; Table 2 → Inner ✓ Enabled, Outer — Not used, Main LED — Not used. It says it cannot be edited here | TC-M02 |
| 3 | Read **Device Mapping** | Four rows only (Table 1 Inner / Outer / Main LED, Table 2 Inner), each with an empty IP box, an empty Shared folder box, a **Not mapped** badge and a **Test** button. No rows for Table 2 Outer / Main LED. One orange button: **SAVE MAPPING**. The other buttons (Test All Connections, Back to Dashboard) are plain outlined | TC-M03 |
| 4 | Fill the four rows. IPs (optional): `10.20.0.11`, `.12`, `.13`, leave Table 2 blank. Folders: `C:\Users\<you>\AppData\Local\Temp\ledsync-shares\T1-Inner`, `…\T1-Outer`, `…\T1-Main`, `…\T2-Inner` (use the real `%TEMP%` path — run `echo $env:TEMP`). Click **SAVE MAPPING** | Green "Device mapping saved." Values are still in the boxes; the badges read **Not tested**. Save again with no change → blue "Nothing changed — the mapping was already saved." | TC-M04 |
| 5 | Click **Test All Connections** | The button shows "Testing…" briefly, then "4 of 4 destinations passed: Connection Successful." Every row shows a green **Connection Successful** badge with the time (your local time, `DD/MM/YY HH:MM`) | TC-M05 |
| 6 | In PowerShell: `Get-ChildItem -Force $env:TEMP\ledsync-shares -Recurse` | The four folders are **empty** — no `.ledsync-probe-….tmp` left behind | TC-M06 |
| 7 | Delete one folder: `Remove-Item $env:TEMP\ledsync-shares\T1-Main`. Back in the app click that row's **Test** | Red message "Table 1 Main LED: Connection Failed — The folder does not exist. Check the shared folder name and path."; that row's badge is red **Connection Failed**; the other rows keep their green status | TC-M07 |
| 8 | Recreate it (`New-Item -ItemType Directory $env:TEMP\ledsync-shares\T1-Main`) and click **Test** on that row again | Green — the status updates | TC-M08 |
| 9 | **Access denied.** Make a folder read-only for you: `icacls "$env:TEMP\ledsync-shares\T1-Outer" /deny "$($env:USERNAME):(OI)(CI)(WD,AD)"` then **Test** on Table 1 Outer | Red: "Access was denied while writing to the folder. Check that this Windows account is allowed to use the shared folder." (reading works, writing is refused). Undo it: `icacls "$env:TEMP\ledsync-shares\T1-Outer" /remove:d $env:USERNAME` and Test again → green | TC-M09 |
| 10 | **Device off / unreachable.** Change Table 2 Inner's folder to `\\192.0.2.1\share` (an address nothing answers) and click **Test** on it. Then set it to `\\no-such-device-xyz\share` and Test again | First: after **about 10 seconds** — "The device did not respond within 10 seconds. Check that it is on and on the network." The window stays usable the whole time. Second: within a few seconds — "The device could not be reached. Check the device is on, on the network, and the name is right." Neither message contains Windows error text | TC-M10 |
| 11 | **Rejected entries** — try each, then **SAVE MAPPING**: (a) IP `999.1.1.1`; (b) folder `relative\folder`; (c) folder `C:\Windows`; (d) the same folder in two rows (upper/lower case differs); (e) folder `\\device\share\..\other`; (f) a very long path | A red message that names the row ("Table 1 Inner: …") and says what to fix; **nothing is saved** (Back to Dashboard and return: the old values are still there); what you typed stays in the boxes; the message is announced and focus moves to it | TC-M11 |
| 12 | **Your real event folders.** Clear the four rows and enter: Table 1 Inner `C:\LED\1000\Table1_inner`, Table 1 Outer `C:\LED\1000\Table1_outer`, Table 1 Main LED `C:\LED\1000\Table1_mainled`, Table 2 Inner `C:\LED\1000\Table2_inner`; **SAVE MAPPING**, then **Test All Connections** | "4 of 4 destinations passed: Connection Successful." All four green; the four folders stay empty (`Get-ChildItem -Force C:\LED\1000 -Recurse`). *Later, with real venue devices (`\<device>\<share>\<folder>`), tell me the result and which Windows account they expect (follow-up F-33).* | TC-M12 |
| 13 | Close (X) and relaunch with **only** these two lines (do **not** repeat the `Remove-Item` line, it would wipe the data): `$env:LEDSYNC_DATA_DIR = "$env:TEMP\ledsync-test-p5"` then `.\.venv\Scripts\python -m ledsync`. Open event 1000 | All folders, IPs, statuses and times are still there | TC-M13 |
| 14 | Close the app. Open `%TEMP%\ledsync-test-p5\ledsync.db` in DB Browser. **`led_mappings`**: 4 rows with `enabled`=1, `ip_address`, `shared_folder`, `connection_status`, `last_connection_test` (UTC `…Z`). **`operation_log`**: *Mapping Saved* (names the destinations, not the paths) and *Device Test* rows. **`exception_log`**: one row per failed test — category *Missing folder* / *Permission* / *Network / device connectivity*, with table, LED type and destination | TC-M14 |
| 15 | Keyboard only: Tab through the page (back link → topbar → the mapping boxes and Test buttons in row order → SAVE MAPPING → Test All → Back). Press Enter in a folder box | Visible focus ring on every control; Enter **saves** (it does not start a test); after an error focus jumps to the message | TC-M15 |
| 16 | *(optional, needs a second exported `_GUID.json` — skip if you have none)* Re-register with a different LED structure | Matching rows keep their folders but show **Not tested**; removed LEDs move under "Previously mapped — no longer part of this event"; new LEDs appear unmapped. (Covered by automated tests TC-A09.) | TC-M16 |

Clean up: close the app, then `icacls "$env:TEMP\ledsync-shares" /reset /T` if you did step 9 and did not undo it, and `Remove-Item -Recurse -Force "$env:TEMP\ledsync-test-p5","$env:TEMP\ledsync-shares"`.

## Summary

| Total cases | Passed | Failed | Blocked | Not yet run |
|---|---|---|---|---|
| 39 | 23 (14 automated groups covering 195 new pytest cases, 1 rendered-page group, 8 real-Windows checks — all run by Claude) | 0 | 0 | 16 (manual TC-M01 – TC-M16, for the owner) |

`.\.venv\Scripts\python -m pytest -q` → **844 passed, 7 skipped** (the 7 are the live-Azure tests, which need `--live`; Phase 5 does not touch Azure).

## Real Windows checks (run by Claude, 21/09/26)

| ID | Check | Result |
|---|---|---|
| TC-W01 | `\\host-nonexistent\share` (name that does not resolve) | Pass — Windows error 53 → "device could not be reached" (2.2 s) |
| TC-W02 | `\\localhost\nosuchshare` (this machine, no such share) | Pass — error 67 → "folder does not exist" |
| TC-W03 | `C:\no\such\folder` | Pass — error 3 → "folder does not exist" |
| TC-W04 | `\\192.0.2.1\share` (unroutable address) — raw Windows call blocks about **34 s**; the app returns in 10 s | Pass — this is why every check runs on a time-limited thread |
| TC-W05 | A real writable folder | Pass — connection successful; folder left empty |
| TC-W06 | Order of classification: "network path not found" (53) is reported by Python as *FileNotFoundError* but means the **device** is unreachable, not that a folder is missing | Pass — fixed after this check showed it mis-labelled (regression test in TC-A05) |
| TC-W07 | A colon inside a folder name (NTFS alternate data stream, `\\d\a:stream`) | Pass — refused (found while writing tests) |
| TC-W08 | Your real folders `C:\LED\1000\Table1_inner`, `Table1_outer`, `Table1_mainled`, `Table2_inner` through the real validation and connection-test code | Pass — all four validate; all four "can be opened and written to"; all four left empty |

## Test cases — automated

| ID | Description | Status |
|---|---|---|
| TC-A01 | **LED structure:** exactly what the file says (real Event 1000: Table 1 all three, Table 2 Inner); tables sorted numerically (1, 2, 10); a table with nothing enabled still shown; event lookup ignores case; unreadable / missing stored configuration fails safely without echoing it; canonical LED names and aliases (`inner`, `Main LED`, `main_led`…) | Pass |
| TC-A02 | **Mapping rows follow the structure:** one unmapped row per enabled LED at registration; repair of a missing row is idempotent; removed LEDs are hidden and keep their data | Pass |
| TC-A03 | **IP validation:** blank OK; IPv4/IPv6 OK; out-of-range, partial, host names, injection text, over-long refused | Pass |
| TC-A04 | **Folder validation:** 8 good forms (UNC, forward slashes, local drive, `$` share) and 35 refused forms (relative, `..`, device paths, drive root, wildcard/control characters, reserved names, trailing dot/space, URLs, OS folders, the application data folder, colon streams, bidi override, over 200 characters) | Pass |
| TC-A05 | **Connection test:** writable/empty/missing/file-not-folder; probe never overwrites, unique name, removed; an un-removable probe is a warning not a failure; 12 Windows error kinds → plain text + BRD §21 category (never Windows error text or the path); read-only share fails at the write step; a hanging device times out without blocking the others; many dead devices share one deadline; a crashing check leaks nothing; **no ping, no sockets, no Azure** | Pass |
| TC-A06 | **Save rules:** persists and reports what changed; IP optional; one bad row saves **nothing**; every bad row reported together; duplicate folders (any case) refused, including against an already-saved destination not in this post; swapping two folders allowed; same folder allowed in a different event; entries for LEDs that are not enabled are ignored; changing a destination resets its status; audit row names destinations only | Pass |
| TC-A07 | **Recording a test:** failure stored, audited and raised as an open exception with table/LED/destination; success raises none | Pass |
| TC-A08 | **Page & routes:** login + launch cookie required; missing CSRF refused and nothing changes; unknown/malformed event IDs → branded 404; case-insensitive event IDs; reserved IDs (`new`, `reregister`) cannot be registered; dashboard rows link to the page | Pass |
| TC-A09 | **Re-registration integration:** matching mappings kept and reset, removed hidden and listed, new unmapped (service level and through the real re-register pages) | Pass |
| TC-A10 | **Forgery and injection:** forged fields for LEDs that are not enabled ignored; forged actions (`delete`, `test:2-Outer`, `test:../x`, empty, wrong case, NUL) refused **before anything is saved**; `<script>` in a path and an attribute-breakout quote are escaped; a 20,000-character value is refused; the application data folder is refused | Pass |
| TC-A11 | **Testing through the page:** Test All records each result, shows plain messages, leaves no debris, raises one exception per failure; a single **Test** saves the typed values first and tests only that row; nothing-to-test messages; an invalid form is not tested; an un-removable probe shows a notice but passes; a dead device is reported within the deadline and the page stays usable; failure messages contain no Windows error text or path | Pass |
| TC-A12 | **Audit without secrets:** no credential words anywhere in the mapping, operation or exception tables | Pass |
| TC-A13 | Earlier phases unchanged (649 earlier tests still pass; the read-only-Azure AST guard, key isolation and CSP tests all still pass with the new pages) | Pass |
| TC-A14 | **Independent-review regression tests** (38): protected folders cannot be reached by an 8.3 short name (`PROGRA~1`), a loopback admin share (`localhost`, `127.x`, this computer's own name), the data folder's short name, or a **junction** (refused at save **and** again at test time; nothing is written through it); two names for one local folder count as a duplicate; a returning hidden LED can never duplicate an enabled folder; a test result is never written onto a folder changed while the test ran (no audit or exception row either); a database failure while recording a result is a plain message, not an error page; the page opens even if mapping rows cannot be created; invisible/bidi/space-lookalike characters, `CON .txt` and `COM` + superscript digits are refused while Arabic folder names still work; Windows error 206 says "path too long"; unchanged saves are not audited; an un-removable probe is audited; the duplicate message names the right destination; signed-out POST goes to login | Pass |

## Rendered-page checks

| ID | Description | Result |
|---|---|---|
| TC-R01 | Event Details: empty, saved, tested (mixed success/failure), rejected form. Bordered inputs, one orange action, status badges readable on black, table fits a 1366-px window without sideways scrolling (`docs/screenshots/phase5-*.jpg`) | Pass |

## Open defects / follow-ups

| ID | Description | Severity | Status |
|---|---|---|---|
| F-32 | **Test only proves what this Windows account can do.** A device that allows the operator's account but not the account used by a scheduled/unattended run (Phase 12) would pass here and fail later. | Medium | Carried to Phase 8 / 12 |
| F-33 | **Venue shared-folder authentication method is still open (BRD §36).** Nothing is stored or logged. If venue devices need their own user name and password, that becomes a security decision (where to store it) before Phase 8. | Medium | Open — needs owner |
| F-35 | Resolving a junction that lives **on a remote share** cannot be done from this machine, so a junction on a venue device pointing into that device's own protected folder would not be detected. Only someone with access to the device can create one. | Low | Accepted |
| F-36 | The listing step (the folder must be listable) fails a share where listing is denied but writing is allowed. This follows the owner's chosen check order. Confirm at the venue that the LED devices' shares are listable. | Low | Open — confirm at the venue |
| F-37 | Repeated clicks on a dead host start extra 10-second checks whose threads live until Windows gives up (about 34 s). Bounded by the operator's clicking; no effect on the rest of the application. | Info | Accepted |
| F-34 | The probe file is written to the *real* destination folder; if the LED device watches that folder and reacts to any new file it could briefly see a file that is removed immediately. Confirm with the venue whether that is harmless. | Low | Open — confirm at the venue |
| F-28 | Storage key stored in plain text (accepted risk, owner). | — | Carried |
| F-29 | Real cloud naming vs BRD (change-log file name `_ledassetschangelog.csv`, lower-case cloud folders, `keepalive.txt`). Needed at Phase 6. | Info | Carried to Phase 6 |
| F-12 | Earlier carry-forwards (single-instance lock, log retention, migration runner) unchanged. | — | Carried forward |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context) | 21/09/26 | **Approved with notes** — no blockers; ran the suite and about 60 probes of its own. Four "fix now" findings reproduced and resolved: protected-folder and data-folder checks bypassed by 8.3 names, loopback admin shares and junctions; a returning hidden mapping breaking the one-folder rule; a stale test result stamped onto a changed folder; database errors giving an error page. Cheap notes also done (invisible characters, reserved names, path length, audit noise, un-removable probe audited, Back-button label, row headers). The fixes were verified by 38 new tests but did **not** get a second independent review pass. |
| User (Vatsan) go-ahead | Vatsan | | *pending manual test* |
