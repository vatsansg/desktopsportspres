# QA Test Case Document — Desktop Phase 4: Azure Storage Connectivity

## Header

| Field | Value |
|---|---|
| Application | Desktop |
| Step / Phase | Phase 4 — Azure Storage Connectivity (Step 4.1 Cloud Storage settings, Step 4.2 live `_GUID.json` download and registration) |
| BRD reference(s) | Desktop BRD Sections 4 (relationship to the web app; storage key), 8, 9, 10, 14 (Cloud Storage Settings), 15/16 (change-log location), 21 (error categories), 33.3 (key at rest), 34 (Business Rule 13: never write to the web app's storage) |
| Implementation Sequence reference | Step 4.1, Step 4.2 (with the 20 Sep 2026 confirmation that the account is `sasportspresentation` and Event 1000 exists) |
| Date | 21/09/26 |
| Tested by | Claude Code (automated + **live against the real Azure account, read-only**); user to run the manual guide below |
| Environment | Windows 11 Pro, Python 3.11.9, pywebview 6.2.1, WebView2 153.x, azure-storage-blob 12.30.2; scratch local database; real storage account `sasportspresentation` (read-only). |

## Owner decisions applied

- **Access key at rest: plain text in the database** (owner decision, 21/09/26) — an explicit, owner-approved departure from BRD 33.3. Mitigations kept: never shown after saving, never logged, never in any error message or page, reachable only through `services/settings.py`, one read/write seam so Windows encryption can replace it later.
- **Storage layout:** the *event storage path* comes from each event's own `eventStorageUrl` (inside `_GUID.json`), so it is not a setting; there is **no Asset storage path setting** (files are located from the change log's relative paths). A new event is found by searching the configured year container, then the other 4-digit year containers, for the folder `<EventID> - …`.
- **Event IDs are case-insensitive** (`abc` = `ABC`; leading zeros stay significant: `01000` ≠ `1000`).
- **Cloud only:** the file picker from Phase 3 is gone; registration downloads from Azure.

## How to test in the real app (for the user)

The key is already in your git-ignored `.env`, so the app can work straight away. The steps below also prove the **Settings screen** works on its own by temporarily hiding `.env`.

```powershell
cd C:\vatsan\techprojects\INFRA\ledassetmanagement\Applications\desktop
Remove-Item -Recurse -Force "$env:TEMP\ledsync-test-p4" -ErrorAction SilentlyContinue
$env:LEDSYNC_DATA_DIR = "$env:TEMP\ledsync-test-p4"
New-Item -ItemType Directory -Force $env:LEDSYNC_DATA_DIR | Out-Null
.\.venv\Scripts\python -m ledsync
```
Sign in, then use **Settings** in the header.

| # | Do this | Expect | Covers |
|---|---|---|---|
| 1 | Open **Settings** (fresh database) | "Cloud Storage" page: Storage account `sasportspresentation` and Access key marked as supplied by **the development .env file**; container `2026`; the key field is empty and never pre-filled. Event storage path / Asset storage path are explained, not editable. One orange **SAVE SETTINGS** button | TC-M01 |
| 2 | Click **Test Connection** (leave the key blank) | Notice: "Connected to storage account sasportspresentation. Year containers found: 2026, 2027. Container 2026 found. Nothing has been saved." | TC-M02 |
| 3 | Close the app. In PowerShell: `Rename-Item .env .env.off` — then relaunch (same commands as above, without deleting the folder) | App starts normally | TC-M03 |
| 4 | **Settings** now | Account/container empty, Access key badge **Not set** (no more ".env" wording) | TC-M03 |
| 5 | Dashboard → **ADD NEW EVENT** → Event ID `1000` → REGISTER EVENT | Red message "Cloud storage is not set up yet…" with an **Open Settings** link; nothing contacted | TC-M04 |
| 6 | In Settings enter account `ab` and click Save; then container `A B`; then key `hello` | Each gives its own clear red message; **what you typed for the key is never shown back**; nothing saved | TC-M05 |
| 7 | Enter account `sasportspresentation`, container `2026` and paste a **wrong** key: `AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==` (88 characters) → **Test Connection** | Red: "Azure refused the storage account key…" — no Azure error text, no key echoed | TC-M06 |
| 8 | Enter the **real key** (paste it into the field; **Show/Hide** works) → **Test Connection** | Green "Connected…" and: "Nothing has been saved — **type the key again and press Save** to keep these settings." (the field is empty again on purpose) | TC-M07 |
| 9 | Paste the real key again → **SAVE SETTINGS** | "Cloud storage settings saved." Access key shows a green **Saved** badge; the key field is empty; the hint says "The key is never shown again" | TC-M08 |
| 10 | Close (X) and relaunch. **Settings** | Account, container and **Saved** badge still there (loaded from the database — `.env` is hidden) | TC-M09 |
| 11 | Dashboard → ADD NEW EVENT → `1000` → REGISTER EVENT | The button changes to **CONTACTING AZURE…**, then the Dashboard shows green "Event 1000 (Star contender Doha) registered from Azure Storage." Row: `1000`, Star contender Doha, **Last Updated 18/09/26** + your local time, badge **Registered** | TC-M10 |
| 12 | Register `1000` again | Blue notice "already registered with this GUID. Nothing changed." | TC-M11 |
| 13 | Register `987654321` | Red: "No folder for event 987654321 was found in container 2026 or the other year containers…" | TC-M12 |
| 14 | Register a blank ID, `../x`, and (paste the key into the Event ID box) | Clear Event ID messages; the pasted key is **refused and not shown back** | TC-M13 |
| 15 | *(optional)* Turn Wi-Fi off (or airplane mode), Add Event `1000` | Within a few seconds: "Could not reach Azure Storage. Check the internet connection…". Turn Wi-Fi on and retry: works | TC-M14 |
| 16 | Close the app. Open `%TEMP%\ledsync-test-p4\ledsync.db` in DB Browser. **`application_settings`**: `cloud_storage_account`, `cloud_container`, `cloud_access_key` (**the key is in plain text here — the accepted deviation**). **`events`**: `configuration_file` is the Azure address of the `_GUID.json` (no key in it) and `configuration_json` the file. **`operation_log`**: *Settings Changed* (says which fields changed, no values), *Cloud Storage Test* (Success/Failed), *Event Registered*. **`exception_log`**: rows for the failed test (Permission), the unknown event (Missing folder), and offline (*Azure Storage connectivity*) | TC-M15 |
| 17 | Keyboard: Tab through Settings (account → container → key → Show → SAVE → Test Connection → Back) | Visible focus ring; after an error or test result, focus jumps to the message | TC-M16 |
| 18 | **Restore `.env`:** `Rename-Item .env.off .env` | (so the live tests and development keep working) | — |

Reset: close the app, then `Remove-Item -Recurse -Force "$env:TEMP\ledsync-test-p4"`.

Live checks (read-only, run by Claude 21/09/26 and re-runnable): `.\.venv\Scripts\python -m pytest -q --live tests/test_live_azure.py`

## Summary

| Total cases | Passed | Failed | Blocked | Not yet run |
|---|---|---|---|---|
| 66 | 66 (42 automated groups covering 231 new pytest cases; 7 live checks against real Azure; 1 rendered-page group; 16 manual TC-M01 – TC-M16 run by the owner on 21/09/26: "All ok") | 0 | 0 | 0 |

`.\.venv\Scripts\python -m pytest -q` → **649 passed, 7 skipped** (the 7 are the live tests, which need `--live`); `--live` → **7 passed** against real Azure. New dependency: `azure-storage-blob` 12.30.2 (24 packages in the runtime set; `pip-audit`: no known vulnerabilities).

## Live validation against the real Azure account (read-only, 21/09/26)

| ID | Check | Result |
|---|---|---|
| TC-L01 | Connect with the real key and list containers | Pass — containers `2026`, `2027`, `desktopinstaller`, `templates`; default `2026` found |
| TC-L02 | Find the event from just its ID | Pass — `1000` → `1000 - Star contender Doha` |
| TC-L03 | Download the real `_GUID.json`, parse it (event 1000; GUID `eb0153b9-…`; Table 1 Inner/Outer/Main, Table 2 Inner) and verify its storage address against the real account | Pass |
| TC-L04 | Unknown event | Pass — clear *Missing folder* error |
| TC-L05 | A wrong key | Pass — Azure refuses; reported as *Permission*; no key or Azure text shown |
| TC-L06 | A wrong account name | Pass — *connectivity* error in **2.3 s** (was ~13 s before the retry fix) |
| TC-L07 | **Step 4.2 end to end through the real app code** (real Azure, throw-away local database): event 1000 registered with every field stored; source address recorded without any credential; second registration a no-op; the key appears nowhere in the local database | Pass |

## Test cases — automated

| ID | Description | Status |
|---|---|---|
| TC-A01 | Read-only Azure client against a faithful fake of the SDK: exact-prefix folder search, year-container order (preferred first, then newest year), non-year containers never searched, missing/preferred-missing containers, ambiguity refused, top-level scan capped | Pass |
| TC-A02 | Downloads: size cap applied to the request itself (max+1 bytes, a 10 MB blob is never fully read); missing `_GUID.json` says "run Export Event"; other missing files get their own message; empty blob handled; relative paths validated (`..`, `\`, absolute, empty segments, control chars, length) | Pass |
| TC-A03 | Error mapping to BRD 21 categories for 13 failure kinds (auth, 401/403, DNS/timeouts/connection resets, 404, 500/503, proxy/sign-in-page replies, unknown); **never echoes SDK text, the account name or the key** | Pass |
| TC-A04 | **Read-only guarantee, AST-based:** no application code calls any mutating or SAS-signing Azure operation, private SDK attributes, dynamic `getattr`/`eval`/`__import__`, or any other HTTP client; Azure is imported only by `storage.py`; `storage.py` calls exactly the six read operations; the guard is itself proven to catch the evasions the old substring test missed | Pass |
| TC-A05 | Settings service: validation (account, container, key incl. base64/length), save/reload across a "restart", blank key keeps the saved key, invalid input saves nothing, all-or-nothing with its audit row, audit names fields but never values, key never in repr/str/logs, refuses to touch any key it does not own | Pass |
| TC-A06 | `.env` / environment fallback: parser, precedence, per-field fallback, **ignored entirely in an installed build**, fallback values validated (a hostile account name like `evil.com/x#` is never used), source labelled correctly | Pass |
| TC-A07 | **Step 4.2 flow on the fake Azure:** real test event registers with every field; found in another year container; only read operations used; not-configured, offline, wrong key, HTTP 500, unknown event, no `_GUID.json`, two folders, oversized blob, bad JSON — each explained, categorised, written to the exception log, never leaking | Pass |
| TC-A08 | **S-14:** a storage address in the file pointing at another host/account/port/container/folder, with a query (`?sig=`), fragment, `%2F`, `..`, `//`, backslash or malformed port is refused with a clear message and logged (a malformed port used to give a 500); a secret query never reaches the database; legitimate spellings (case, `:443`, trailing slash) still verify | Pass |
| TC-A09 | **Case-insensitive Event IDs:** Azure lookup matches any typed case; exact ID boundary kept (`EVT90` ≠ `EVT9`); two folders differing by case = ambiguous; registering `evt9` after `EVT9` is the same event (and re-registers the same row on a new GUID); database refuses two IDs differing only by case; a database that already contains such duplicates still starts | Pass |
| TC-A10 | Settings screen: gated; branded; only one primary action; save/persist; key never shown on any page, header, cookie or log after saving; blank key keeps it; invalid input rejected without echoing; **Test Connection** reads only, doesn't save, says so, works with a typed or saved key, reports wrong key/offline; audit rows without values | Pass |
| TC-A11 | **Key isolation:** only `settings.py` mentions `cloud_access_key`; `auth.py` and `settings.py` never touch each other's keys (guard tests); log call sites never format the key; pages get a **key-less** view of the settings | Pass |
| TC-A12 | A key pasted into the wrong field (Event ID, account, container) is refused and never echoed or stored | Pass |
| TC-A13 | Offline venue fails fast: one retry, 8 s connect / 20 s read timeouts (worst case well under the old ~37 s); busy state cleared on Back/bfcache restore; Azure SDK request logging suppressed | Pass |
| TC-A14 | Settings/cloud/storage layers import without Flask (headless scheduled run, Phase 12) | Pass |
| TC-A15 | Phase 3 registration/re-registration behaviour unchanged, now via the cloud flow (all Phase 3 route tests moved onto it) | Pass |

## Rendered-page checks

| ID | Description | Result |
|---|---|---|
| TC-R01 | Settings (empty, saved, invalid, test success, test wrong key) and Add Event (not configured, offline): bordered inputs, no box-in-box clutter, key field never pre-filled, one orange action (`docs/screenshots/phase4-*.jpg`) | Pass |

## Open defects / follow-ups

| ID | Description | Severity | Status |
|---|---|---|---|
| F-27 | Manual checks TC-M01 – TC-M16 run by the owner. | Info | Closed — passed |
| F-28 | **Key stored in plain text (owner decision).** Anyone who can read `ledsync.db` (default: only this Windows user's profile) gets a key with broad read *and write* power over the storage account. Read-only is a rule this application keeps, not something the key enforces. Recommended before venue deployment: a read-only, container-scoped credential from the web-app team, and/or Windows encryption (one place to change). | Medium | Accepted by owner; revisit before Phase 13 |
| F-29 | **Real cloud naming differs from the BRD** (found while validating): the change-log file is `_ledassetschangelog.csv` (with an "s"); cloud folders are lowercase (`Table 1/inner`, `outer`, `mainled`) while change-log entries use `Inner/Outer/MainLED`; there are `keepalive.txt` placeholders and a file without an extension (`Table 1/mainled/HOME_Look`). Decisions needed at Phase 5/6: case-insensitive path matching, local folder naming, ignoring `keepalive.txt`. | Medium | **Owner decision needed at Step 5/6** |
| F-30 | Persist the verified `(container, folder)` per event and use it (never `config.storage_url`) for every later download; add blob properties, listing under a folder, and streamed downloads with a size cap (needed Phase 6–7). | Info | Carried |
| F-31 | Move HTTPS redirects/proxy/TLS-interception handling and Windows certificate-store behaviour into the Phase 13 installer checks (frozen build must package certifi and the cffi hooks). | Low | Carried to Phase 13 |
| F-12 | Earlier carry-forwards (single-instance lock, log retention, migration runner, data-dir vs headless decision) unchanged. | — | Carried forward |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context) | 21/09/26 | **Approved with notes** — no blockers; ran the suite and probed with its own fakes and the real SDK over a local no-network transport; the access key never leaked on any path tried. Ten "fix now" findings resolved (crash and looseness in address verification, case-insensitive Azure lookup, Test Connection honesty, dev-only fallback and its validation, echo of pasted keys, offline hang, evadable read-only test, bfcache busy state, SDK log noise, generic `read_blob`); notes carried as F-30/F-31. The fixes have not had a second independent pass |
| User (Vatsan) go-ahead | Vatsan | 21/09/26 | Approved — "All ok, close pending items and move to next step" |
