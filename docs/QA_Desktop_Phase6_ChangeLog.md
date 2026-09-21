# QA Test Case Document — Desktop Phase 6: Change Log & Incremental Comparison

## Header

| Field | Value |
|---|---|
| Application | Desktop |
| Step / Phase | Phase 6 — Change Log and Incremental Download Logic (Step 6.1 retrieve and parse the cloud change log, Step 6.2 local change log, Step 6.3 incremental comparison). **Table / LED file downloads are Phase 7; the owner asked for the event's `RPI` files to be downloaded now (see below).** |
| BRD reference(s) | Desktop BRD Sections 15 (steps 4–5), 16 (cloud change log, v2.4 full-path note), 17 (local change log), 18 (incremental logic), 21 (error categories), 36 (open points: cut-off, local change log filename) |
| Implementation Sequence reference | Steps 6.1, 6.2, 6.3 |
| Date | 21/09/26 |
| Tested by | Claude Code (automated; **live against the real Azure account, read-only**); user to run the manual guide below |
| Environment | Windows 11 Pro, Python 3.11.9, pywebview 6.2.1, WebView2 153.x; scratch local database; real storage account `sasportspresentation` (read-only) for the live check; fake Azure for everything else. |

## Owner decisions applied (21/09/26)

- **Time basis / cut-off:** compare the change log's own **UTC** timestamps; never this computer's clock; **no cut-off** yet (the setting arrives with Phase 10, default "none"). Times are *shown* in this computer's local time.
- **Deleted entries:** listed, and queued as **"Delete local copy"** when a successful local copy older than the deletion exists. **Nothing is deleted in this phase** — there are no local copies yet; the deletion itself is done in Phase 7 (with a safe-path guard). LED-device folders are not touched by this rule.
- **Local change log:** the **database is the record** (`download_history`); **`_localchangelog.csv`** in the application data folder is its CSV copy with the BRD §17 columns, one file for all events.
- **Screen:** a separate **Change Log** page per event (link on the Event Details page), one orange **CHECK FOR CHANGES** button.
- **RPI files (owner, 21/09/26):** `RPI/<file>` entries are real assets that belong in an **RPI folder on this computer**, set in **Settings → Local Folders** (default: an `RPI` folder inside the application data folder). A **Update RPI Files** button on the Change Log page downloads new/updated RPI files and removes the local copy of RPI files deleted in the cloud. **Files with no extension are not assets** and are ignored (listed as not applicable). Table and LED files are still not downloaded here.
- Defaults I chose (tell me if any is wrong): the real file name `_ledassetschangelog.csv` is read first, the BRD spelling second; a file is identified by its **full path** matched **ignoring case**; entries that are not exactly `Table N/<LED type>/<file>` for a table and LED type the event enables are **Not applicable** (never acted on); the RPI folder is **flat** (`<RPI folder>\<file name>`, shared by all events, as you described it); the check is read-only and its result is kept in memory (a restart clears it — the next phase re-checks before downloading).

## How to test in the real app (for the user)

This uses the **real** Event 1000 change log in Azure (read-only — the app cannot write to Azure).

```powershell
cd C:\vatsan\techprojects\INFRA\ledassetmanagement\Applications\desktop
Remove-Item -Recurse -Force "$env:TEMP\ledsync-test-p6" -ErrorAction SilentlyContinue
$env:LEDSYNC_DATA_DIR = "$env:TEMP\ledsync-test-p6"
New-Item -ItemType Directory -Force $env:LEDSYNC_DATA_DIR | Out-Null
.\.venv\Scripts\python -m ledsync
```
Sign in. The key comes from your git-ignored `.env`, so **ADD NEW EVENT → `1000` → REGISTER EVENT** works straight away.

| # | Do this | Expect | Covers |
|---|---|---|---|
| 1 | Click event `1000` on the Dashboard | Event Details opens; under the title: "Event 1000 · Registered · **Change Log**" (a link) | TC-M01 |
| 2 | Click **Change Log** | A page titled **Change Log**: "Check For Changes" with one orange **CHECK FOR CHANGES** button and a plain **Back to Event**; "No check has been run yet in this session" | TC-M02 |
| 3 | Open `%TEMP%\ledsync-test-p6\_localchangelog.csv` in Notepad (it already exists — created when the app started) | One line, the BRD headers: `Serial Number, Event ID, Table, LED Type, File Name, Source Location, Local Location, Timestamp, Source Updated Timestamp, Download Status, Sync Status`; no data rows | TC-M03 |
| 4 | Click **CHECK FOR CHANGES** | The button changes to **CONTACTING AZURE…**, then a green message: "Checked 84 change log entries: 51 to download, 0 to remove, 1 already processed. Nothing was downloaded or changed." *(numbers are as the real log stood on 21/09/26; they grow if the web team adds files)* | TC-M04 |
| 5 | Read **Summary** | "Checked <local time> … file _ledassetschangelog.csv · 84 entries covering 53 distinct files." Chips: **New 51 · Updated 0 · Removed In Cloud 0 · Already Processed 1 · Not Applicable 1 · Unreadable Rows 0** | TC-M05 |
| 6 | Scroll **Waiting To Be Processed (51)** | Table / LED / File / Changed / Cloud Status / Next Step. Rows for Table 1 Inner, Outer and Main LED. **`sponsorsequence.csv` appears twice — once under Inner, once under Outer** (distinct entries). Each row's Next Step is a blue **New** with "Not downloaded yet." | TC-M06 |
| 7 | Open **Already Processed (1)** | `gamebreak.mp4` (Table 1 Inner): "Removed in the cloud; nothing is stored locally." | TC-M07 |
| 8 | Open **Not Applicable To This Event (1)** | `RPI/HOME_Look.png` — "The file is not inside a Table / LED-type folder." (it is in the real log but not in any table) | TC-M08 |
| 9 | Press **F5** / reopen the page | The same result appears with no "CONTACTING AZURE" delay (kept in memory) | TC-M09 |
| 10 | Close the app. In DB Browser open `%TEMP%\ledsync-test-p6\ledsync.db`. **`download_history`** is empty; **`operation_log`** has a *Check Changes* row: "51 to download, 0 to remove, 1 already processed, 1 not applicable, 0 unreadable row(s) in _ledassetschangelog.csv."; **`exception_log`** has no rows. The data folder contains only `ledsync.db` (and its `-wal/-shm`), `_localchangelog.csv` and the application's log files — **no asset files and no `Table` / `Inner` / `Outer` folders** | TC-M10 |
| 11 | **See "already processed", "updated" and "removed":** `.\.venv\Scripts\python scripts\insert_test_history.py` (adds three pretend downloads to this scratch database), then relaunch the app (same launch lines, **without** the `Remove-Item` line) and press CHECK FOR CHANGES again | Chips now: **New 49 · Updated 1 · Removed In Cloud 1 · Already Processed 1**. `FFTT.png` (Table 1 Inner) moves to Already Processed; `gamebreak.mp4` (Table 1 Inner) now shows a red **Removed in cloud** — "the local copy will be deleted"; `sponsorsequence.csv` **Table 1 Outer** shows **Updated** — and **Table 1 Inner's `sponsorsequence.csv` is still New** (an update to one folder does not mask or trigger the other). Waiting To Be Processed is still 51 (49 + 1 + 1) | TC-M11 |
| 12 | Open `_localchangelog.csv` again | Now three data rows (Serial 1–3, Event 1000, `Table 1`, `Inner`/`Outer`, the file names, Download Status `Success`) | TC-M12 |
| 13 | Remove the pretend rows: `.\.venv\Scripts\python scripts\insert_test_history.py --clear`, relaunch, check again | Back to the numbers in step 4 | TC-M13 |
| 14 | **Wrong key:** Settings → enter a wrong key → Save; then Change Log → CHECK FOR CHANGES. (Re-enter the real key afterwards.) | Red: "Azure refused the storage account key…" — no Azure text, no key | TC-M14 |
| 15 | *(optional)* turn Wi-Fi off, CHECK FOR CHANGES | Within seconds: "Could not reach Azure Storage. Check the internet connection…"; on again → works | TC-M15 |
| 16 | Keyboard only: Tab through the page (back link → topbar → CHECK FOR CHANGES → Back to Event → the summary → tables and the "Already processed"/"Not applicable" sections) | Visible focus ring on every control; Enter/Space opens the sections | TC-M16 |

**RPI steps (new)** — continue in the same app window (remove the pretend rows first with `insert_test_history.py --clear` if you added them):

| # | Do this | Expect | Covers |
|---|---|---|---|
| 17 | Click **Settings** → **Local Folders** | A page with the RPI folder box (empty), "Files are saved to `%TEMP%\ledsync-test-p6\RPI`" and a **Default folder** badge; one orange **SAVE FOLDERS** | TC-M17 |
| 18 | Type `relative\RPI` → SAVE FOLDERS | Red message, the text stays in the box, nothing saved. Also try `C:\Windows\RPI` → refused | TC-M18 |
| 19 | Event 1000 → Change Log → CHECK FOR CHANGES | An **RPI Files** section shows the folder and a plain **Update RPI Files (1)** button. In the waiting list the row reads `—` / `RPI` / `HOME_Look.png`. `Table 1/MainLED/HOME_Look` (no extension) is under Not Applicable: "The file has no extension, so it is not an asset." | TC-M19 |
| 20 | Click **Update RPI Files (1)** | Green: "RPI files: 1 downloaded, 0 removed, 0 failed. Saved in …\RPI." Open `%TEMP%\ledsync-test-p6\RPI` — **`HOME_Look.png` is there and opens as the image** (about 480 KB). The section now says "No RPI files are waiting." and the Waiting list drops by one. Nothing else was downloaded (no Table folders) | TC-M20 |
| 21 | Press the button's page again / CHECK FOR CHANGES | "No RPI files are waiting."; the file is listed under **Already Processed** | TC-M21 |
| 22 | Open `%TEMP%\ledsync-test-p6\_localchangelog.csv` and the database | One data row: Event `1000`, Table empty, LED Type `RPI`, File `HOME_Look.png`, Download Status `Success`. `download_history` has the same row; `operation_log` has an *RPI Files* row | TC-M22 |
| 23 | **Your own folder:** close the app, start it again with a **fresh** scratch data folder (`$env:LEDSYNC_DATA_DIR = "$env:TEMP\ledsync-test-p6b"`, create it, `python -m ledsync`), register `1000`, then **Settings → Local Folders → `C:\LED\RPI` → SAVE FOLDERS** ("Your folder" badge), then Change Log → CHECK FOR CHANGES → **Download RPI Files** | `HOME_Look.png` appears in **`C:\LED\RPI`** (created if missing) and **not** in the default folder | TC-M23 |

Clean up: close the app, then `Remove-Item -Recurse -Force "$env:TEMP\ledsync-test-p6"`.

**About the two-table validation case** (Sequence Step 6.1: "a test event with two tables, each with `sponsorsequence.csv` in Inner and Outer"): the real Event 1000 has that file in **Table 1** Inner and Outer only, and I cannot write test data to your Azure. The four-distinct-entries case, and the "update in one folder only" case, are covered by automated tests (TC-A04) on synthetic logs. If you want to see it on real data, ask the web-app team to add `sponsorsequence.csv` to Table 2's folders.

Live check (read-only, re-runnable): `.\.venv\Scripts\python -m pytest -q --live tests/test_live_azure.py`

## Summary

| Total cases | Passed | Failed | Blocked | Not yet run |
|---|---|---|---|---|
| 44 | 21 (16 automated groups covering 345 new pytest cases, 4 live checks against real Azure, 1 rendered-page group — all run by Claude) | 0 | 0 | 23 (manual TC-M01 – TC-M23, for the owner) |

`.\.venv\Scripts\python -m pytest -q` → **1189 passed, 9 skipped** (the 9 are the live-Azure tests, which need `--live`); `--live` → **9 passed** against real Azure (read-only).

## Live validation against the real Azure account (read-only, 21/09/26)

| ID | Check | Result |
|---|---|---|
| TC-L01 | Download the real `_ledassetschangelog.csv` of Event 1000 through the real app code and parse it | Pass — 84 rows, 0 unreadable; columns `sno, filename, changetimestamp, status, username`; 58 New / 21 Updated / 5 Deleted; timestamps UTC |
| TC-L02 | Compare against an empty local record | Pass — 53 distinct files; `Table 1/Inner/sponsorsequence.csv` and `Table 1/Outer/sponsorsequence.csv` are **separate** entries; `RPI/HOME_Look.png` is **Not applicable**; a file with no extension (`Table 1/MainLED/HOME_Look`) is listed as an ordinary asset |
| TC-L04 | **The real RPI file:** the real `RPI/HOME_Look.png` (Azure holds it as lower-case `rpi/HOME_Look.png`) is located by name, downloaded read-only and saved as `HOME_Look.png` in the scratch RPI folder | Pass — **489,580 bytes, valid PNG**; the only file in the folder; history and local change log rows correct; no key anywhere; the real application data folder untouched |
| TC-L03 | The whole check through the real page, with the real key | Pass — nothing written to Azure; no key anywhere in the page; `download_history` stays empty; `_localchangelog.csv` has only its header row |

## Test cases — automated

| ID | Description | Status |
|---|---|---|
| TC-A01 | **Parsing (6.1):** the real exported file (83 rows) parses completely; BOM, Windows line endings, quoted names with commas, extra/re-ordered columns, header case/spaces, blank lines, a missing username column, non-numeric `sno`; timestamps read as UTC instants (with `Z`, offset, fraction, or no zone) | Pass |
| TC-A02 | **A file that cannot be understood** (empty, wrong columns, no timestamp column, invalid UTF-8, NUL, over 5 MB, over 50,000 rows) is refused with a plain message and logged; **14 kinds of bad row** (empty/`..`/absolute/`//`/backslash/bidi/zero-width/control characters, over-long, unknown status, unreadable or out-of-range time, short row) are **skipped, counted and explained without echoing their content**, and the rest of the file is still used | Pass |
| TC-A03 | **Fetching:** real file name first, BRD spelling as fallback, no change log = clear "no change log yet", Azure errors pass through unchanged, the request itself is capped, only read operations are used | Pass |
| TC-A04 | **Comparison (6.3):** four distinct `sponsorsequence.csv` (Tables 1–2 × Inner/Outer); an update in one table/LED folder queues **only that one** and never masks the others; mixes of new / updated / processed; latest entry wins regardless of row order; case-insensitive matching; equal timestamps are processed and later ones are not; instants not text (offset vs `Z`); deletion after a successful download queues a local delete; deletion with nothing local, a processed deletion, an older deletion, and a re-add after removal | Pass |
| TC-A05 | **Not applicable:** outside any table, in a sub-folder, a table or LED type the event does not use, unknown LED folder, mis-spelt table folder; LED folder case and Main LED spellings; leading-zero table numbers; ordering (table, LED, name; not-applicable last) | Pass |
| TC-A06 | **What "local" means:** only successful downloads and deletions count (failures do not); the latest record wins; unreadable history rows are ignored; another event's history is never used; comparing changes nothing in the database | Pass |
| TC-A07 | **Local change log (6.2):** created at start-up in the data folder with exactly the BRD §17 columns and no rows; start-up never creates a database just for it; mirrors `download_history`; **formula-looking file names neutralised** (`= + - @`); atomic rewrite leaves no temporary file; a blocked or locked file is reported, never fatal; a deleted file is repaired from the database | Pass |
| TC-A08 | **Page and routes:** login + launch cookie required; missing CSRF refused and contacts nothing; unknown/malformed events → branded 404; case-insensitive event IDs; link from Event Details; one orange button; result survives a reload without contacting Azure; a second check replaces the first; each event has its own result | Pass |
| TC-A09 | **The check on real-shaped data:** the real exported log (83 entries) lists both `sponsorsequence.csv` per folder as distinct and the odd entry as not applicable; exact counts for a known log with history (new / updated / removed / processed / not applicable); unreadable rows shown with line numbers; long lists capped on screen (500) but counted in full | Pass |
| TC-A10 | **Failures are plain, categorised and logged:** no change log, event re-exported (GUID changed → "re-register first"), unusable file, Azure unreachable (no Azure text), wrong key (*Permission*), cloud storage not set up (Azure not contacted), a storage address in the file pointing elsewhere is not trusted | Pass |
| TC-A11 | **Safety:** a check only reads Azure and changes no file, no history, no mapping, no event; it is audited with counts only; hostile file names (`<script>`, attribute breakouts, formulas) are escaped | Pass |
| TC-A12 | **Seed script (dev only):** refuses without a scratch folder, in the real data folder, in a folder that does not exist, and when the event is not registered; adds three marked rows once; `--clear` removes only those | Pass |
| TC-A14 | **Independent-review regression tests** (83): 22 Windows-dangerous names skipped and reported, 17 ordinary real-world names still accepted, a path is never trimmed, over-long paths refused; look-alike table folders (`Table 01`, `0001`, `0`, Arabic-Indic digit) never create a second download; six pairs of names Windows could treat as one file (`ß`/`ss`, Kelvin sign, sigma, micro, long s, accent forms) are listed as not applicable, never merged or queued; a database error never empties the local change log (existing file untouched; headers created only if none exists); ten formula-start characters neutralised; an unexpected exception is a plain message with an exception row and a *Failed* audit row, and a failure to log never hides the real message; a saved result is dropped when the registration or local history changes; same-time entries settle by file order; a removal older than the local copy keeps the copy; removals are listed before downloads; far-future entries are pointed out | Pass |
| TC-A15 | **RPI (65 tests):** real blob names found in any A–Z letter case, ambiguous names refused, folder-vs-file, prefix neighbours, unsafe paths refused before any listing, only reads; the safe writer (atomic replace, 14 unsafe names refused for write **and** delete, a folder or **junction** with the file name is never replaced, followed or deleted, a failed write leaves no temp file); folder rules (created when missing, Windows folders and the data folder refused); the setting (default inside the data folder, save/reset, 8 unsafe folders refused, audit, own-keys scope); the Settings pages (gated, navigation, default shown, save, bad folder explained); the whole flow (download to the default and to a chosen folder, history + local change log + last-download, second press does nothing, update replaces, deletion removes then a re-add restores, one bad file never stops the rest and is retried, too-large refused with the request itself capped, **only RPI files are touched**, extension-less and sub-folder entries ignored, an unusable folder is a plain message, hostile names never reach the disk, only reads Azure, gated and CSRF-protected, a re-exported event downloads nothing) | Pass |
| TC-A16 | **RPI review regression tests (82):** the data folder refused under a different-looking real path, folders containing it, drive roots, OS folders (by identity), a hard-linked database, a real `subst` drive; every spelling of "this computer" (`0`, `127.1`, decimal, hex, octal); `CONIN$`/`CONOUT$`; a second event never overwrites or removes the first event's file (its removal is recorded but the file kept); a deleted-by-hand file and a changed folder are downloaded again while present or deliberately removed files are left alone; an over-long folder is refused not cut; 21 dangerous file types refused by parser **and** writer while ordinary media is accepted; 8.3 aliases refused and never touch another file; a connection problem stops the run after one call; one run is limited and the next continues; listings shared across files; file-level ambiguity; fsync; stale temp sweep; read-only and disk-full messages; a folder that does not answer gives up | Pass |
| TC-A13 | Earlier phases unchanged (844 earlier tests still pass; Azure read-only AST guards, key isolation, CSP all still pass with the new modules) | Pass |

## Rendered-page checks

| ID | Description | Result |
|---|---|---|
| TC-R01 | Change Log: before a check, after a check on the real log, with unreadable rows, with a missing change log. One orange action; readable badges on black; bordered controls; the real 84-entry result fits without side scrolling (`docs/screenshots/phase6-*.jpg`) | Pass |

## Open defects / follow-ups

| ID | Description | Severity | Status |
|---|---|---|---|
| F-38 | **Deleted in the cloud → delete the local copy** (owner decision) is only *decided* here. Phase 7 must carry it out safely: only inside the event's own local asset folder, never following links, never outside it, and never touching the LED-device folders (whether the push should also remove files there is not decided — Phase 8). | Medium | Carried to Phase 7/8 |
| F-39 | The **Last Updated Timestamp Cut-off** setting (BRD §14/§18) does not exist yet; comparison is UTC with no cut-off (owner choice). Add the setting in Phase 10 (default none). | Low | Carried to Phase 10 |
| F-40 | `RPI/HOME_Look.png` is a real RPI asset (now downloaded to the RPI folder) and the extension-less `Table 1/MainLED/HOME_Look` is not relevant (ignored) — both settled by the owner 21/09/26. | — | Closed — decided |
| F-41 | **Sync Status** in `_localchangelog.csv` is blank until Phase 8 records pushes. | Info | Carried to Phase 8 |
| F-42 | The change log says `Table 1/Inner/…` but the blob folders in Azure are lower-case (`Table 1/inner/…`). Phase 7 must locate blobs case-insensitively (as Phase 4 does for the event folder) and download only from the verified location. | Medium | Carried to Phase 7 (F-29) |
| F-43 | In `_localchangelog.csv` **Timestamp** is this computer's local time (`DD/MM/YY HH:MM`, BRD example) while **Source Updated Timestamp** is the change log's own UTC text. Say if you want both in one basis. | Info | Open — confirm |
| F-44 | The last check result is kept in memory only; after a restart the page says "No check has been run yet". Phase 7 always re-reads the change log before downloading. | Info | Accepted |
| F-45 | A change log with **more than 50,000 rows** is refused as a whole (an append-only log could eventually reach that). One CSV field over 128 KB, or a stray quote that swallows the next row, also affects the file (the swallowed row is reported once). Far beyond real use (the real log has 84 rows). | Low | Accepted — revisit if the log grows |
| F-46 | Phase 7 must **re-validate every name at the point of writing** (never trust this parser alone), refuse links, stay inside the event's folder, and resolve the real blob name (Azure names are case-sensitive; the comparison ignores A–Z case). | Medium | Carried to Phase 7 |
| F-47 | A history row whose source time cannot be read is ignored, so its file would be downloaded again. Cannot happen through the application (Phase 7 will write valid times). | Info | Accepted |
| F-48 | **Table 2 Inner has 24 asset files in Azure with NO change-log entry** (the log has one entry for Table 2, `sponsorsequence.csv`; the 24 images/videos uploaded to `Table 2/inner/` were never logged). A download driven by the change log will never fetch them. Found on 21/09/26 by comparing Azure's file list with the log (read-only). Either the web application must log them (it should when assets are added or copied) or Phase 7 needs a fallback that also compares Azure's file list. | **High** | **Open — decision needed (web team / Phase 7)** |
| F-49 | The **RPI folder is flat and shared by all events** (as you described). Two events with the same RPI file name cannot both have it: the second is **refused with a clear message** (the review showed silent overwriting), and a second event's removal never deletes the first's file. Decide if you want per-event sub-folders instead, or "newest event replaces". | Medium | Open — owner decision |
| F-50 | A whole file is held in memory while downloading (50 MB cap for RPI). Large Table/LED videos need streaming in Phase 7. | Medium | Carried to Phase 7 |
| F-12 | Earlier carry-forwards (single-instance lock, log retention, migration runner) unchanged. | — | Carried forward |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context) | 21/09/26 | **Approved with notes** — no blockers; ran the suite and about 40 probes of its own. Six "fix now" findings reproduced and resolved: file names that are legal in a URL but dangerous on Windows (trailing dot, `:stream`, `NUL`/`COM1`, illegal characters, over-long names) were accepted as downloads — one overwrote another file in its probe; `Table 01` / `Table 0001` / Unicode digits created several downloads of one file; case-folding merged files Windows keeps apart (`ß`/`ss`, Kelvin sign); a database error emptied `_localchangelog.csv`; an unexpected error gave an error page and no audit row; the in-memory result was never invalidated. Cheap notes also done (tie-breaks, future-dated entries pointed out, removals listed first, wrong "already processed" wording, more formula starts, unique temp file). The fixes were verified by 83 new tests but did **not** get a second independent review pass. |
| Second independent review (RPI download and local file writes) | Independent review agent (fresh context) | 21/09/26 | **Second independent review (RPI download + local file writes): Approved with notes, conditional on four fixes — all done.** Reproduced and fixed: a **mapped drive / `\\localhost\C$` alias could hide the data folder** (a downloaded `ledsync.db` replaced the database) — folders are now judged by file identity, and the data folder, anything containing it, drive roots, operating-system folders and any folder where the database shows up as a plain file are refused; **a second event overwrote or removed the first event's file** in the shared RPI folder — now refused/kept; **a file deleted by hand or a changed RPI folder never re-downloaded** — now detected; **a long folder was silently truncated when saved** — now refused. Plus hardening: dangerous file types (`.exe .lnk .url .scf desktop.ini` …) and **8.3 short-name aliases** refused, a connection problem stops the run (and a run is limited to 200 files), listings cached, fsync + stale-temp sweep, precise read-only/disk messages, time limits on folders that do not answer. 82 new tests; **not re-reviewed**. |
| User (Vatsan) go-ahead | Vatsan | 21/09/26 | Approved on condition that the RPI files download and the RPI folder setting were in place ("once the files in RPI is downloaded, and the configuration is included for the local folder for rpi, then i am ok for you to … complete this step, commit and go to the next step"). Both were delivered and proven against the real Azure file. **The owner had not run the manual steps TC-M01 – TC-M23 at that point; they stay in this document to run at any time.** |
