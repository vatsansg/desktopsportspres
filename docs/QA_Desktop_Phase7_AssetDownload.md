# QA Test Case Document — Desktop Phase 7: Asset Download

## Header

| Field | Value |
|---|---|
| Application | Desktop |
| Step / Phase | Phase 7 — Asset Download, **download only, no push to LED yet** (Step 7.1 download the identified files, Step 7.2 local asset structure, Step 7.3 update the local change log) |
| BRD reference(s) | Desktop BRD Sections 15 (steps 1–11), 17 (local change log), 18 (incremental logic), 19 (local asset storage), 21 (error categories), 34 (Business Rule 13: read-only against Azure) |
| Implementation Sequence reference | Steps 7.1, 7.2, 7.3 |
| Date | 21/09/26 |
| Tested by | Claude Code (automated; **live against the real Azure account, read-only — including the whole real Event 1000, 76 files / 630 MB**); user to run the manual guide below |
| Environment | Windows 11 Pro, Python 3.11.9, pywebview 6.2.1, WebView2 153.x; scratch local folders; real storage account `sasportspresentation` (read-only) for the live checks; fake Azure for everything else. |

## Owner decisions applied (21/09/26)

- **Files missing from the change log:** Azure's own file list is compared too. Files that exist in an enabled Table/LED folder of the event in Azure but have **no change-log entry** are offered as **"New (not in log)"** unless already downloaded (found on the real event: **24 assets in Table 2 Inner** were never logged). The change log always wins for any path it mentions.
- **Local layout:** `<Asset folder>\<Event ID>\Table N\<Inner | Outer | Main LED>\<file>`. The **Asset folder** is a setting (**Settings → Local Folders**), default `Events` inside the application data folder. Only tables and LED types the event enables get a folder.
- **RPI files:** now `<RPI folder>\<Event ID>\<file>` — **a sub-folder per event** (changed from the flat folder built earlier, so two events can never clash).
- **Integrity:** every file must match Azure's listed **size**, and Azure's **MD5** when it lists one; a wrong download is discarded, retried once, and reported. The previous copy of a file is never touched by a failed download.
- **Deleted in the cloud → the local copy is deleted** (owner decision from Phase 6), inside the event's own folder only.
- Defaults I chose (tell me if any is wrong): one **DOWNLOAD FILES** button downloads Table/LED files and RPI files together, in the background with a progress display and **Cancel**; one automatic retry per file, and a connection or permission problem ends the run; a file the history says was downloaded but that is no longer on disk (deleted by hand, or the folder was changed) is downloaded again; the two folders in Settings must be different and neither may be inside the other; nothing is sent to the LED devices (Phase 8).

## How to test in the real app (for the user)

This uses the **real** Event 1000 in Azure (read-only — the app cannot write to Azure). The full event is about **630 MB** (82 files), so use a scratch folder with enough free space.

```powershell
cd C:\vatsan\techprojects\INFRA\ledassetmanagement\Applications\desktop
Remove-Item -Recurse -Force "$env:TEMP\ledsync-test-p7" -ErrorAction SilentlyContinue
$env:LEDSYNC_DATA_DIR = "$env:TEMP\ledsync-test-p7"
New-Item -ItemType Directory -Force $env:LEDSYNC_DATA_DIR | Out-Null
.\.venv\Scripts\python -m ledsync
```
Sign in, then **ADD NEW EVENT → `1000` → REGISTER EVENT** (your `.env` key works straight away).

| # | Do this | Expect | Covers |
|---|---|---|---|
| 1 | **Settings → Local Folders** | Two boxes, **Asset folder** and **RPI folder**, both empty; under them "Asset files go to `%TEMP%\ledsync-test-p7\Events`" and "RPI files go to `…\RPI`", each with a **Default folder** badge; one orange **SAVE FOLDERS** | TC-M01 |
| 2 | Type the **same** folder in both boxes (e.g. `C:\LED\Same`) → SAVE; then `relative\x`; then `C:\Windows\Events` | Each refused with a red message; what you typed stays; nothing saved. (The two folders must be different and neither inside the other.) Clear the boxes again | TC-M02 |
| 3 | Event 1000 → **Change Log** → **CHECK FOR CHANGES** | Green message "Checked 87 change log entries: …". Chips include **New, Not In Log 24** (the Table 2 Inner assets nobody logged). A **Download Files** section shows both destination folders and an orange **DOWNLOAD FILES (76)** *(the number is as the real event stood on 21/09/26)*; **Check Again** is now plain | TC-M03 |
| 4 | Look at **Waiting To Be Processed** | Rows for Table 1 Inner / Outer / Main LED, Table 2 Inner and one `—` / `RPI` row (`HOME_Look.png`). Table 2 rows such as `App.png` say "New (not in log) — In Azure but not in the change log." `keepalive.txt` and the extension-less `HOME_Look` are not listed as downloads | TC-M04 |
| 5 | Click **DOWNLOAD FILES (76)** | The page changes to **Download In Progress**: "Downloading file N of 76: <name> (x%)" with a bar, and a plain **Cancel Download**. The window stays usable. It finishes in a few minutes (about 1½ minutes on a fast line) and reloads by itself | TC-M05 |
| 6 | Read the result | Green: "Downloaded 76 file(s), removed 0, failed 0." The section now says "Nothing is waiting to be downloaded." and **CHECK FOR CHANGES** is orange again | TC-M06 |
| 7 | In PowerShell: `Get-ChildItem "$env:TEMP\ledsync-test-p7\Events\1000" -Recurse -Directory \| Select-Object -ExpandProperty FullName` and `Get-ChildItem "$env:TEMP\ledsync-test-p7\Events" -Recurse -File \| Measure-Object -Property Length -Sum` | Folders exactly: `Table 1\Inner`, `Table 1\Outer`, `Table 1\Main LED`, `Table 2\Inner` — **no** `Table 2\Outer`, no other folders. **75 files**, about 630 MB. RPI: `…\ledsync-test-p7\RPI\1000\HOME_Look.png` | TC-M07 |
| 8 | Open a few files: an image, a video from Table 1 Inner, `sponsorsequence.csv` in Table 1 Inner and in Table 2 Inner | They open and play; the two `sponsorsequence.csv` files have **different** content (same name, different tables) | TC-M08 |
| 9 | Press **CHECK FOR CHANGES** again | Nothing waiting; the files are under **Already Processed** | TC-M09 |
| 10 | Delete one downloaded file by hand (e.g. any `.png` in `Table 1\Inner`), press **CHECK FOR CHANGES** | **DOWNLOAD FILES (1)**; click it → it returns; nothing else is downloaded again | TC-M10 |
| 11 | Open `%TEMP%\ledsync-test-p7\_localchangelog.csv` in Notepad, and `ledsync.db` in DB Browser | The CSV has **76 data rows**: Event `1000`, `Table 1`/`Table 2` (empty for RPI), LED Type `Inner`/`Outer`/`Main LED`/`RPI`, file name, source address (no key), local path, local time, source time, `Success`. `download_history` has the same rows; `operation_log` has *Asset Download* and *RPI Files* rows; `exception_log` is empty; `events.last_download` is set | TC-M11 |
| 12 | **Cancel:** close the app, `Remove-Item -Recurse -Force "$env:TEMP\ledsync-test-p7"`, relaunch (same lines), register `1000` again, check, click DOWNLOAD FILES and after a few seconds click **Cancel Download** | "Cancelling after the current file…", then "Cancelled; N file(s) were not downloaded. Nothing half-written was kept." `Get-ChildItem "$env:TEMP\ledsync-test-p7" -Recurse -Filter *.ledsync-tmp` finds nothing. Check + DOWNLOAD again finishes the rest | TC-M12 |
| 13 | **Your own folders:** on a fresh data folder set **Asset folder** `C:\LED\Events` and **RPI folder** `C:\LED\RPI` → SAVE FOLDERS ("Your folder" badges), then download | Files appear under `C:\LED\Events\1000\…` and `C:\LED\RPI\1000\HOME_Look.png` and **not** in the default folders. (Your device folders `C:\LED\1000\Table…` from Phase 5 are separate and untouched) | TC-M13 |
| 14 | **Connection problem:** start a download and switch Wi-Fi off after a few files | After one retry: "Stopped after a connection or permission problem; N file(s) were not tried. Check the connection and download again." Switch Wi-Fi on, check, download → the rest arrives | TC-M14 |
| 15 | Keyboard only: Tab through Change Log (buttons, tables, progress region) | Visible focus ring; the progress text is announced politely; Cancel is reachable | TC-M15 |

Clean up: close the app, then `Remove-Item -Recurse -Force "$env:TEMP\ledsync-test-p7"` (and `C:\LED\Events`, `C:\LED\RPI` if you used them).

Live checks (read-only, re-runnable): `.\.venv\Scripts\python -m pytest -q --live tests/test_live_azure.py` (the full 630 MB run needs `$env:LEDSYNC_LIVE_FULL = "1"`).

## Summary

| Total cases | Passed | Failed | Blocked | Not yet run |
|---|---|---|---|---|
| 35 | 35 (8 automated groups covering about 130 new pytest cases, 4 live checks against real Azure, 1 rendered-page group — all run by Claude; 15 manual TC-M01 – TC-M15 run by the owner on 21/09/26: "All good") | 0 | 0 | 0 |

`.\.venv\Scripts\python -m pytest -q` → **1314 passed, 12 skipped** (the skipped are the live-Azure tests, which need `--live`); `--live` → **all live tests pass** against real Azure (read-only); the full 630 MB run needs `$env:LEDSYNC_LIVE_FULL = "1"`.

## Live validation against the real Azure account (read-only, 21/09/26)

| ID | Check | Result |
|---|---|---|
| TC-L01 | The real event's listings carry size and MD5 | Pass — 82 files, 630 MB; MD5 on 80 of 82 (the two without are small `sponsorsequence.csv` files, checked by size) |
| TC-L02 | The check finds the real files the change log never mentions | Pass — Table 2 Inner assets offered as "New (not in log)" |
| TC-L03 | A real selection (Table 1 Main LED, the RPI file, Table 2 files) downloaded through the real engine and re-checked against Azure's size and MD5 | Pass — 5 of 5 verified; **`default.png` exists in Table 1 Main LED (946 KB) and Table 2 Inner (132 KB) and both were stored correctly** |
| TC-L04 | **The whole real event through the page** (Check, then DOWNLOAD FILES) | Pass — **76 files (75 Table/LED + 1 RPI), 0 failed, in about 100 s** (run twice — before and after the review fixes); every file's size and MD5 equals Azure's; folders exactly `Table 1\{Inner, Outer, Main LED}` and `Table 2\Inner`; RPI in `RPI\1000\`; a second run downloads 0 files |

## Test cases — automated

| ID | Description | Status |
|---|---|---|
| TC-A01 | **Azure listings and ranged reads:** size, MD5, last-modified and version listed; folders and files found in any A–Z letter case, only files listed, missing folder = no files, two folders differing only in case refused, unsafe paths refused before any call, listings shared across lookups, files read in 4 MB ranges never whole, empty file needs no read, missing file / lost connection are plain errors | Pass |
| TC-A02 | **Verified streaming writes:** size and MD5 checked before the file takes its name; wrong size or checksum discarded and the old copy kept; no fingerprint = size only; empty file; cancel between chunks and errors while downloading leave nothing behind; disk-space check; unsafe names, folders with the file's name refused | Pass |
| TC-A03 | **Sub-folders and settings:** nested folders created only from validated names, 10 unsafe names refused, a file or **junction** with a folder name refused; the asset folder default (`Events`) and both folders saved together, reset by blanking; the two folders must differ and not nest (also through junction and 8.3 aliases); unsafe folders and a folder containing the data folder refused; audit rows | Pass |
| TC-A04 | **Azure-list comparison:** files only in Azure offered as "New (not in log)" with their **real** Azure path (including `Main LED`); the log always wins; placeholders, unsafe names and non-assets left out (unsafe ones **counted and shown**); only enabled tables/LED folders listed; both `Main LED` spellings; already-downloaded files not re-offered; names that would be one file on disk, and the same name in two folders, reported not merged; a listing problem is reported and the log result stands; 3,000 unlogged files in seconds | Pass |
| TC-A05 | **Download engine:** all and only the new files land in `<event>\Table N\<LED>`; only the event's tables/LED types get folders; the same file name in four folders stays four files; history, source address (no key), local path, time recorded; second run does nothing; an update replaces only that file; a cloud removal deletes only that file (never through a junction); deleted-by-hand files come back; gone-everywhere files are dropped once; a bad file never stops the rest and is retried; a corrupt download is retried once, reported, old copy kept; a transient failure is survived; a 9 MB file needs 3 ranged reads; no fingerprint accepted on size; a file replaced during the download is detected and the retry gets the new version (also without MD5); connection problems and Azure 429/500/503 stop the run after one retry; cancel stops between files; progress reports files and bytes; dangerous roots refused; two events keep separate folders; only reads Azure | Pass |
| TC-A06 | **Job, routes and page:** check-then-download through the page puts every file in place; summary shown once (and never lost); history and `_localchangelog.csv` cover every kind of file; chosen folders used; removals reported; single-file problems listed while the rest arrive; an unusable folder is a plain message; a re-exported event downloads nothing; login/launch-cookie/CSRF on every new route; a GET never starts a download; a real worker thread runs to completion and can be polled; one job per event; the progress panel with Cancel; crashes end in an error state; both folders on the Settings page | Pass |
| TC-A07 | **Independent-review regression tests (34):** Azure-only `Main LED` download; junction removal refused; temp files swept in sub-folders (user files untouched); the summary race; gone-everywhere files; retry sees fresh listing; replaced-during-download; 429/500/503; stop/cancel accounting and no slow tidy-up; error styling; no-JS fallback and accessible progress; settings identity checks; over-long paths; FIPS-mode checksum; bounded page disk checks; error mapping | Pass |
| TC-A08 | Earlier phases unchanged (the Phase 6 RPI tests were updated for per-event folders; the Azure read-only AST guards, key isolation and CSP all pass with the new code) | Pass |

## Rendered-page checks

| ID | Description | Result |
|---|---|---|
| TC-R01 | Change Log after a check (orange DOWNLOAD FILES, plain Check Again), in progress (progress panel, Cancel), Settings → Local Folders (two folders). One orange action per screen; bordered inputs; readable badges; progress bar visible on black (`docs/screenshots/phase7-*.jpg`) | Pass |

## Open defects / follow-ups

| ID | Description | Severity | Status |
|---|---|---|---|
| F-48 | 24 assets in Azure `Table 2/inner/` had no change-log entry. | High | **Closed** — Azure's file list is compared; the web team can still fix the log |
| F-49 | Flat RPI folder shared by all events. | Medium | **Closed** — owner decision: `<RPI folder>\<Event ID>\` |
| F-50 | Large files were held in memory. | Medium | **Closed** — 4 MB ranged streaming |
| F-46 | Re-validate every name at the write site; never trust the parser alone. | Medium | **Closed** — `localfiles` re-checks names, folders, links and lengths |
| F-38 | Cloud deletions carried out safely. | Medium | **Closed** — inside the event's folder only, never through a link |
| F-42 | Locate blobs by case-insensitive name. | Medium | **Closed** — done by listing (any A–Z case; ambiguous refused) |
| F-51 | An Azure-only file **overwritten in Azure without a change-log entry** is not re-downloaded (its history holds Azure's last-modified time from the first download). The change log is the way to signal an update; ask the web team to log every change. | Low | Accepted — design |
| F-52 | Streaming writes have **no overall time limit**: a dead network destination could hold the download inside a write (folder operations and the final move are time-limited; cancel is honoured between chunks). Also unverified against real Azure: blobs with `Content-Encoding`. | Low | Accepted |
| F-53 | The download summary is shown **once** — the first page view consumes it (like a flash message); a second browser tab sees nothing. The history, `_localchangelog.csv` and logs keep the record. | Info | Accepted |
| F-54 | Real network-share aliases (`\\localhost\C$`, a mapped drive) of the data folder are covered by file-identity checks and by a simulated alias plus a real `subst` drive; a real SMB alias was not exercised on this machine. | Low | Accepted |
| F-55 | **Nothing is pushed to the LED devices** — that is Phase 8 (needs the shared folders you mapped in Phase 5). | — | Next phase |
| F-12 | Earlier carry-forwards (single-instance lock, log retention, migration runner) unchanged. | — | Carried forward |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context) | 21/09/26 | **Approved with notes** — no blockers. Five "fix now" findings reproduced and fixed: Azure-only files in a `Main LED` folder were offered but could never be downloaded; a cloud removal followed a **junction** at Table/LED level and deleted a file outside the asset tree; stale temporary files in sub-folders were never swept; a finished job's **summary could be lost** in a race; a file missing from disk and from Azure kept the run "waiting" forever. Worthwhile notes also done: the retry re-lists (no stale cache), a **file replaced in Azure during a download is detected** (version pinning), Azure 429/5xx stop the run, a stop/cancel skips the slow tidy-up and counts every file not tried, folder settings compared by real location, over-long paths and FIPS-mode checksums handled, accessibility of the progress panel, structured message severity. 34 new tests; **not re-reviewed**. |
| User (Vatsan) go-ahead | Vatsan | 21/09/26 | Approved — "All good, complete work in current step, commit and then move to next step" |
