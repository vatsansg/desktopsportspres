# QA Test Case Document — Desktop Phase 8: Push To LED Devices

## Header

| Field | Value |
|---|---|
| Application | Desktop |
| Step / Phase | Phase 8 — Push to LED Devices (Step 8.1 push downloaded files to the mapped shared folders, Step 8.2 the end-to-end **Download & Sync** from the Dashboard with Operation Status) |
| BRD reference(s) | Desktop BRD Sections 15 (Download & Sync steps 12–16), 16 (Synchronise), 17 (local change log Sync Status), 18 (incremental logic), 20 (Dashboard, Operation Status), 21 (error categories), 34 (Business Rule 13: read-only against Azure) |
| Implementation Sequence reference | Steps 8.1, 8.2 |
| Date | 21/09/26 |
| Tested by | Claude Code (automated; **live against the real Azure account, read-only — including the whole real Event 1000, downloaded and pushed to scratch device folders**); user to run the manual guide below |
| Environment | Windows 11 Pro, Python 3.11.9, pywebview 6.2.1, WebView2 153.x; scratch folders as "devices"; real storage account `sasportspresentation` (read-only) for the live checks; fake Azure for everything else. |

## Owner decisions applied (21/09/26)

- **Venue login:** the shared folders are reached with the Windows login the application runs under. No credentials are stored or asked for.
- **Removal from a device:** when the cloud deletes a file, it is removed from a device **only if this application put it there** (recorded in `sync_history`). Anything else on a device is never touched.
- **Verification:** every pushed file is read back from the device and its **size and MD5** compared with the local original before it takes its name; a copy that does not match is discarded and the old file on the device is kept.
- **Same name already on the device that we did not put there:** it is **replaced** (the device must show what the cloud says).
- **Event status** is computed from real state: **Registered** (mapped, not yet tested), **Ready** (all devices tested OK, or files waiting to be sent), **Synced** (everything downloaded has reached its device), **Attention needed** (a device test, download or push failed and has not since been resolved).
- Defaults I chose (tell me if any is wrong): the RPI files stay on this computer (they are not pushed to a Table/LED device); a file is pushed again only when it was downloaded again after its last successful push, or its device folder changed; one automatic retry per file; a network problem with a device fails only that device's remaining files (they are marked "Not sent: an earlier file to this device failed.") while other devices carry on; **Download & Sync** always checks Azure fresh, downloads, and only then pushes — a stopped or cancelled download does not start the push; **Sync To LED Devices** on the Change Log page pushes what is already downloaded and needs no cloud settings.

## How to test in the real app (for the user)

This uses the **real** Event 1000 in Azure (read-only — the app cannot write to Azure). The full event is about **630 MB** (76 files to download), and the push copies **75 files** to your device folders, so make sure they have the space. **Your device folders are written to** — use scratch folders first (steps 1–12), then your real ones (step 13).

```powershell
cd C:\vatsan\techprojects\INFRA\ledassetmanagement\Applications\desktop
Remove-Item -Recurse -Force "$env:TEMP\ledsync-test-p8","$env:TEMP\ledsync-devices" -ErrorAction SilentlyContinue
$env:LEDSYNC_DATA_DIR = "$env:TEMP\ledsync-test-p8"
New-Item -ItemType Directory -Force $env:LEDSYNC_DATA_DIR | Out-Null
foreach ($d in "t1i","t1o","t1m","t2i") { New-Item -ItemType Directory -Force "$env:TEMP\ledsync-devices\$d" | Out-Null }
.\.venv\Scripts\python -m ledsync
```
Sign in, then **ADD NEW EVENT → `1000` → REGISTER EVENT**.

| # | Do this | Expect | Covers |
|---|---|---|---|
| 1 | **Dashboard** | The event row has a new **Actions** column with a plain **Download & Sync** button (the only orange button is ADD NEW EVENT). Below, **Operation Status**: "No operation is running…" | TC-M01 |
| 2 | Open event **1000 → Device Mapping**; type the four scratch folders (`%TEMP%\ledsync-devices\t1i` for Table 1 Inner, `…\t1o` Outer, `…\t1m` Main LED, `…\t2i` Table 2 Inner) and press **SAVE AND TEST ALL** | All four "OK". Back on the Dashboard the status is **Ready** | TC-M02 |
| 3 | On the Dashboard click **Download & Sync** | The Operation Status panel appears at once: "Event 1000 · Checking / Downloading / Synchronising", the file being copied with a percentage, a bar, and counters **Identified · Downloaded · Synchronised · Errors**, plus **Cancel**. The window stays usable | TC-M03 |
| 4 | Wait for it to finish (a few minutes) | A green line at the top: "Downloaded 76 file(s), removed 0, failed 0." and "Synchronised 75 file(s) to the LED devices, removed 0, failed 0." Status becomes **Synced** | TC-M04 |
| 5 | In PowerShell: `Get-ChildItem "$env:TEMP\ledsync-devices" -Recurse -File \| Group-Object Directory \| Select Name,Count` | The four folders hold **24, 25, 1 and 25** files (Table 1 Inner, Outer, Main LED, Table 2 Inner). No hidden `.…ledsync-tmp` files anywhere | TC-M05 |
| 6 | Compare one big video: `Get-FileHash` of the copy in a device folder and of the one under `%TEMP%\ledsync-test-p8\Events\1000\Table 1\Inner` | Identical hashes. The RPI file `HOME_Look.png` is only in `…\ledsync-test-p8\RPI\1000` — **not** on a device | TC-M06 |
| 7 | Click **Download & Sync** again | Green: "Downloaded 0 file(s)… Synchronised 0 file(s)…". Nothing is copied again | TC-M07 |
| 8 | Open `%TEMP%\ledsync-test-p8\_localchangelog.csv` | The **Sync Status** column reads `Success` for the 75 Table/LED files and is empty for the RPI file. `sync_history` in `ledsync.db` has 75 rows whose destination is the full device path | TC-M08 |
| 9 | **A file already on a device:** put a file called `App.png` with other content into `%TEMP%\ledsync-devices\t2i` (replacing that device's copy), then click **Download & Sync** | Nothing is re-sent — the application sends what is new or changed and does not watch devices. (A same-named file is replaced only when that file is sent, e.g. after a new download of it) | TC-M09 |
| 10 | **A changed device folder:** in Device Mapping change Table 1 Main LED to a **new** empty folder, save, then click **Download & Sync** | Only the Table 1 Main LED file is sent, into the new folder | TC-M10 |
| 11 | **A dead device:** repeat the setup lines to start on a fresh data folder, register `1000`, map the four scratch folders, then rename `%TEMP%\ledsync-devices\t1o` to `t1o-gone` and click **Download & Sync** | The other three devices receive their files; the summary is red: "Synchronised 50 file(s) … failed 25" and "Could not use the device folder for: Table 1 Outer." Status **Attention needed**. Rename the folder back and click **Download & Sync** → the missing 25 arrive; status **Synced** | TC-M11 |
| 12 | **Cancel:** with a fresh data folder start **Download & Sync** and press **Cancel** during the push | "Cancelling after the current file…" then "Cancelled; N file(s) were not sent. Nothing half-written was left on a device." `Get-ChildItem "$env:TEMP\ledsync-devices" -Recurse -Force -Filter *.ledsync-tmp` finds nothing | TC-M12 |
| 13 | **Your own device folders:** on a fresh data folder map `C:\LED\1000\Table1_inner`, `Table1_outer`, `Table1_mainled` and `Table2_inner` (Table 1 Inner / Outer / Main LED and Table 2 Inner) → **SAVE AND TEST ALL**, then **Download & Sync** | The files appear in exactly those folders; anything else already in them is untouched (apart from a same-named file, which is replaced) | TC-M13 |
| 14 | **Only push what is downloaded:** Change Log page → **Sync To LED Devices (N)** (available after a download when files are waiting) | Runs the push alone, with progress; afterwards "Nothing is waiting to be sent to the LED devices." An LED with no device folder is named ("No device folder is set for: …") | TC-M14 |
| 15 | Keyboard only: Tab through the Dashboard (Download & Sync, Cancel, Operation Status) | Visible focus ring; the progress is announced politely; Cancel is reachable | TC-M15 |

Clean up: close the app, then `Remove-Item -Recurse -Force "$env:TEMP\ledsync-test-p8","$env:TEMP\ledsync-devices"` (and the files you pushed to `C:\LED\…` if you do not want them).

Live checks (read-only, re-runnable): `.\.venv\Scripts\python -m pytest -q --live tests/test_live_azure.py -k phase8` (the whole-event run needs `$env:LEDSYNC_LIVE_FULL = "1"`).

## Summary

| Total cases | Passed | Failed | Blocked | Not yet run |
|---|---|---|---|---|
| 43 | 28 (automated groups covering about 60 new pytest cases, 2 live checks against real Azure, 1 rendered-page group — all run by Claude) | 0 | 0 | 15 (manual TC-M01 – TC-M15, for the owner) |

`.\.venv\Scripts\python -m pytest -q` → **1375 passed, 13 skipped** (the skipped are the live-Azure tests, which need `--live`); `--live` → all live tests pass against real Azure (read-only).

## Live validation against the real Azure account (read-only, 21/09/26)

| ID | Check | Result |
|---|---|---|
| TC-L01 | A real selection (Table 1 Main LED and Table 2 files) downloaded, pushed to four scratch "device" folders, and each device copy compared with Azure's own size and MD5 | Pass — 4 of 4 verified; no temporary files left; a second push sends nothing; no key in `sync_history` |
| TC-L02 | **The whole real event through the Dashboard's Download & Sync** | Pass — **Downloaded 76 (75 Table/LED + 1 RPI), failed 0; Synchronised 75, failed 0; status Synced** in about 80 s; devices hold 24 / 25 / 1 / 25 files (Table 1 Inner / Outer / Main LED, Table 2 Inner) |

## Test cases — automated

| ID | Description | Status |
|---|---|---|
| TC-A01 | **Planning:** new files ordered by table, LED and name; RPI files and LEDs the event does not enable are never planned; an LED with files but no device folder is reported, not planned; a file already sent is not planned again until it is downloaded again; a failed push is planned again; removals only for files this application sent; a changed device folder re-plans for the new folder; destination matching ignores case | Pass |
| TC-A02 | **Copying one file:** copied, size and MD5 read back, no temporary file left; a 9 MB file goes in three pieces; a same-named file we did not put there is replaced and neighbours are untouched; a copy that loses data is detected, discarded and the old file kept; a missing local copy is a plain error; a device folder that does not exist is reported and never created; a read-only file on the device gives a specific message; unsafe names (`NUL`, streams, `.exe`, 8.3 aliases, sub-folders) and a folder with the file's name are refused; cancel leaves the old file and no temporary file; not enough space is said before copying | Pass |
| TC-A03 | **Device-folder safety:** the data folder, its parent, an OS folder and a **junction to the data folder** are refused; a missing folder is a plain message | Pass |
| TC-A04 | **A whole push:** each file reaches its own device (same name in two tables stays two files), history rows hold the full device path, `last_sync` and audit rows set, devices tested first; a second run sends nothing; a dead device fails only its own files, is tested once and comes back later; a transient failure is retried; after a network failure the rest of that device is not attempted but other devices carry on; a corrupt copy is retried once then reported; a cloud removal removes only what we sent and leaves everything else; removing a file already gone is fine; a device folder that is the data folder is refused; cancel between files; progress counts; a missing local copy fails only that file | Pass |
| TC-A05 | **Event status:** follows the real state through Registered → Ready → Synced; a failed device test, download or push means Attention needed until resolved; the refresh never raises | Pass |
| TC-A06 | **Local change log Sync Status** shows Success / Failure / empty per downloaded file | Pass |
| TC-A07 | **Download & Sync through the app:** downloads then delivers to every device, RPI stays put; a second run does nothing; a dead device is reported, others get their files, status Attention needed then Synced; an LED without a device folder is named and its files stay downloaded; a download that stops early does not start the sync; push-only needs no cloud settings and never calls Azure; saving a mapping and testing keep the status current | Pass |
| TC-A08 | **Dashboard and routes:** operations endpoint needs login and shows running jobs with counters and percent; the panel, Cancel and no-script fallback render; summary shown once; login, launch cookie and CSRF on every route; unknown event 404; a second start while running is refused; Cancel stops the job; the Change Log page shows the sync section; only Azure reads, no stray temporary files | Pass |
| TC-A09 | **Independent-review regression tests (11):** a device folder overlapping (inside, equal to, or containing) the local asset folder is refused and no downloaded original is touched; saving such a folder in the page is refused; a device check that times out fails only that device; a failed removal is planned again and the status recovers; a file whose later push failed is still removed when the cloud removes it; the status recovers after a folder change; a folder shared with another mapping is never cleaned by a removal; not Synced while an LED with files has no folder; RPI-only download is not Synced; a stalled copy is abandoned with a network error and tidies its temporary file; the Operation Status announces only its status line and the button name contains its visible text. Four earlier weak tests were made real (row order, cancel actually stops the job, nothing written outside the asset/RPI/record files) | Pass |
| TC-A10 | Earlier phases unchanged (three earlier tests updated for the new Actions column, crash message and progress fields; Azure read-only AST guards, key isolation, CSP all pass) | Pass |

## Rendered-page checks

| ID | Description | Result |
|---|---|---|
| TC-R01 | Dashboard with the Actions column and Operation Status while running (counters, bar, Cancel) and after a run (Synced badge, empty panel). One orange action per screen; bordered controls; readable on black (`docs/screenshots/phase8-*.png`) | Pass |

## Open defects / follow-ups

| ID | Description | Severity | Status |
|---|---|---|---|
| F-56 | The application does not watch the devices: a file deleted from a device by hand is not re-sent (only new or changed downloads are). | Low | Accepted — design |
| F-57 | A copy has no overall time limit (large files legitimately take minutes) but is **watched**: no progress for 60 s abandons it as a network failure for that device and the abandoned copy removes its own temporary file. A cancel is honoured between chunks, or after that 60 s if the share has stalled. | Low | **Closed by review fix** |
| F-59 | A removal deletes the plain file of that name even if someone replaced it after our push (it is not re-verified). | Low | Accepted — owner rule is "only files this app pushed" |
| F-60 | A re-download in the **same second** as the push that follows it is not re-pushed (timestamps have one-second resolution). A push normally follows the download by more than a second. | Low | Accepted |
| F-61 | Files this application pushed stay on the **old** device folder when a mapping is changed or an LED is hidden (removals look at the current folder only). | Low | Accepted |
| F-62 | A dead device writes one failure row per file (up to 5,000 per run). | Info | Accepted |
| F-58 | Real network-share (`\\venue\share`) behaviour was exercised with local folders and simulated failures; no real SMB share was available here — the owner's `C:\LED\…` folders cover the real venue layout. | Low | Accepted |
| F-51/F-53 | Carried from Phase 7 (Azure-only file overwritten without a log entry; summary shown once). | Low | Accepted |
| F-12 | Earlier carry-forwards (single-instance lock, log retention, migration runner) unchanged. | — | Carried forward |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context) | 21/09/26 | **Approved with notes after fixes** — one blocker and seven "fix now" findings, all reproduced by running code and all fixed: **a device folder overlapping another LED's local asset folder let a push overwrite the downloaded originals and a cloud removal then delete them** (device folders may not overlap the asset or RPI folders any more, at save and at push time); a device check that timed out aborted the whole run instead of failing only that device; a failed removal was never retried and left the status stuck; the status could never clear after a folder change; a stalled copy froze the run (now watched, 60 s); a removal from a folder shared with another mapping (or event) is refused; the event showed Synced while an LED had files but no folder (and RPI-only downloads counted); the Operation Status re-announced every second and a button's accessible name did not contain its visible text. 11 regression tests added; **the fixes were not re-reviewed** (verified by the suite and the live real-event run, re-run after the fixes: 76 downloaded, 75 pushed, 0 failed). |
| User (Vatsan) go-ahead | Vatsan | pending | pending |
