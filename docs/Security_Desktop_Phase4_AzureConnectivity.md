# Security Checklist — Desktop Phase 4: Azure Storage Connectivity

Release/hand-off: Phase 4 — Azure Storage Connectivity (Step 4.1 settings, Step 4.2 live registration). Date: 21/09/26. Application: Desktop.
Status values: **Pass**, **Fail**, **N/A at this stage**, **Accepted risk** (the two BRD-accepted items, plus any the owner explicitly signs off — one is recorded below).

**Theme of this phase:** the application now holds a powerful credential (the Storage Account key) and talks to the internet. Two things must stay true: the key never leaks, and the application only ever reads.

## A. Identity & access (Web Application)

| # | Item | Status | Notes |
|---|---|---|---|
| A1–A6 | Web-application items | N/A at this stage | Web app only. |

## B. Identity & access (Desktop Application)

| # | Item | Status | Notes |
|---|---|---|---|
| B1 | Storage Account access key not committed and protected in local configuration | **Pass for source control; Accepted risk for storage at rest (owner sign-off 21/09/26)** | **Source control:** the key exists only in the git-ignored `.env` (created by the owner with `scripts\set_storage_key.ps1`, masked input, ACL restricted to the owner's account) and, if entered in Settings, in the local database. `.env` is ignored (`git check-ignore`), `git status` shows it as ignored, the secret-scan test passes over everything git could commit, and no test contains a key-shaped literal (the scan caught one during development and it was fixed). **At rest — ACCEPTED RISK:** the owner chose to store the key as **plain text** in `application_settings` (departing from BRD 33.3). Exposure: anyone who can read `ledsync.db` obtains a key with full power over the storage account (Azure keys can write as well as read). Mitigations in place: default location is the current Windows user's profile; never shown after saving; never logged; single read/write seam (`_read_secret`/`_write_secret`) so Windows encryption can replace it in one place. **Recommendation carried:** before venue deployment, obtain a read-only, container-scoped credential and/or enable Windows encryption (F-28). |
| B2 | Accepted-risk plain-text admin credential has not expanded in scope | **Accepted risk — scope unchanged** | Two owners, each confined to its own keys, enforced by tests: `services/auth.py` (admin credential) and `services/settings.py` (cloud settings and key). Neither can read the other's rows (`settings.py` raises `PermissionError` on any key it does not own — a real raise that survives `python -O`); neither references the other's key names; nothing else may mention `application_settings`. The admin credential is still used only for the local login. |
| B3 | Shared-folder credentials not logged | N/A at this stage | Phase 5/8. |

## C. Network & transport

| # | Item | Status | Notes |
|---|---|---|---|
| C1–C4 | Local server transport | N/A at this stage | Unchanged: loopback only, exact Host, launch gate, CSRF, session epoch. All new routes (`/settings/cloud`, `/events/new`) are behind login and the launch cookie (tested). |
| C5 | **Outbound connections to Azure** | **Pass** | HTTPS only to `https://<account>.blob.core.windows.net`, where `<account>` passed the Azure naming rule (3–24 lower-case letters/digits) — so a hostile setting cannot redirect signed requests elsewhere (a hostile `.env`/environment value such as `evil.com/x#` is ignored). Timeouts 8 s connect / 20 s read, one retry: an offline venue fails in a few seconds (measured 2.3 s), not minutes. |
| C6 | **Untrusted storage address inside `_GUID.json`** (finding S-14) | **Pass (after fix)** | The file's `eventStorageUrl` is never used to fetch anything. It is verified to be exactly `https://<configured account>.blob.core.windows.net/<container>/<folder>` — the place it was downloaded from — with no credentials, no port other than 443, no query/fragment/parameters, and the path compared segment by segment before decoding (an encoded `%2F` cannot smuggle a different path). A malformed port used to crash (HTTP 500) and a `?sig=` query used to be accepted and stored; both fixed and tested. Phase 6–8 must use the verified container/folder, never rebuild an address from the file. |

## D. Data protection

| # | Item | Status | Notes |
|---|---|---|---|
| D1 | Storage containers are not public | N/A at this stage | Web-app/Azure side. |
| D2 | Data at rest | **Accepted risk** | See B1 (key). |
| D3 | Values from Azure validated before use | **Pass** | `_GUID.json` parsed exactly as in Phase 3 (strict, size-capped). Blob and folder names from listings are never displayed. Relative paths that will come from the change log are validated by `EventLocation.blob_path` (no `..`, `\`, absolute, empty segments, control characters, over-length). |
| D4 | Downloads bounded | **Pass** | The size cap is applied to the download request itself (`max+1` bytes): a 10 MB blob is never fully read. |

## E. Application security

| # | Item | Status | Notes |
|---|---|---|---|
| E1 | Input validation / injection | **Pass** | Account, container and key validated server-side (Azure naming rules; key must be valid base64 of the right shape). SQL parameterised. Event ID restricted to `[A-Za-z0-9._-]`. |
| E5 | Errors do not leak internals or the key | **Pass** | Every Azure/SDK failure is mapped to a fixed, plain message with a BRD §21 category; **SDK exception text is never shown or logged** (it can contain request URLs/IDs). Tested with exception messages containing a "secret" marker and the key itself. |
| E9 (added) | **Read-only against Azure** (BRD 4, Business Rule 13) | **Pass** | `storage.py` exposes only listing and downloading, and calls exactly six read operations. An **AST-based** test (immune to aliasing and string tricks; proven to catch them) forbids, across all application code: every mutating Azure operation and SAS/signing call, private SDK attributes (`_client`, `pipeline`, `send_request`…), dynamic `getattr`/`eval`/`__import__`, any other HTTP client (`requests`, `urllib.request`, `http.client`, `socket`, `ssl`…), and Azure imports outside `storage.py`. The test fake refuses any write call. **Limit, stated plainly:** the key itself would permit writes; read-only is a rule the code keeps (see F-28). |
| E10 (added) | **Key travels only where intended** | **Pass** | Traced form → validator → database → SDK client, and verified absent from: every HTML page (including error pages, flash messages and form re-population), response headers, the session cookie, `operation_log`, `exception_log`, the diagnostic log, `repr`/`str`, URLs, redirects. Pages receive a **key-less view** of the settings so a future template edit cannot print it. A typed key is never echoed on validation failure, and a key **pasted into the wrong field** (Event ID, account, container) is refused and never shown or stored. Test Connection uses a typed key without saving it and says so. The independent review attempted to make it leak on every path it could think of and could not. |
| E11 (added) | Development fallback cannot silently configure a production machine | **Pass (after fix)** | The `.env`/environment fallback is ignored entirely in an installed (frozen) build, is validated exactly like typed input, and the Settings page names the true source ("the process environment" or "the development .env file"). |

## F. Secrets management

| # | Item | Status | Notes |
|---|---|---|---|
| F1 | No secrets committed | **Pass** | See B1. `.env`, `.env.*`, credential patterns and `OBSERVATIONS.txt` are ignored; `.env.example` holds no value. |
| F2 | Remaining secrets stored appropriately | **Accepted risk (owner)** | See B1. |
| F3 | Logs do not print secrets | **Pass** | Azure SDK request logging (which prints URLs and headers) is suppressed to warnings; the SDK redacts the key and query parameters anyway (verified by the reviewer). Log call sites never format the key (AST-checked). The one-time launch token remains out of all logs. |
| F4 (added) | Key hand-off avoids chat and shell history | **Pass** | `scripts\set_storage_key.ps1` prompts with masked input, refuses to write unless git ignores `.env`, restricts the file to the owner's Windows account, and never prints the key. The key was never pasted into this conversation, and the live tests print nothing sensitive (verified with output masking and `--assert=plain`). |

## G. Logging, monitoring & audit

| # | Item | Status | Notes |
|---|---|---|---|
| G2 | Failed/rejected actions logged without sensitive data | **Pass** | Azure failures → `exception_log` with the right BRD §21 category (*Azure Storage connectivity*, *Permission*, *Missing folder*, *Download*, *Configuration*, *Invalid configuration*), operation, event and source `Azure Storage: <account>` — no key, no SDK text. |
| G4 | Audit completeness and atomicity | **Pass** | Settings changes and their audit row are one transaction; the row names the fields changed (*storage account, container, access key*) and **never a value**. Test Connection results are logged (Success/Failed). |

## H. Dependency & platform

| # | Item | Status | Notes |
|---|---|---|---|
| H1 | No known-critical vulnerabilities in new dependencies | **Pass** | `azure-storage-blob==12.30.2` added (pinned). Runtime closure is now 24 packages (adds azure-core, cryptography, cffi, requests, urllib3, certifi, idna, charset-normalizer, isodate). `pip-audit` on the full pinned closure: **no known vulnerabilities** (21/09/26). Phase 13 must confirm the frozen build packages certifi and the cffi hooks. |
| H2 | Supported runtime | Pass | Python 3.11.9 (security support to Oct 2027). |

## Findings

| ID | Severity | Finding | Action |
|---|---|---|---|
| S-4 | Medium → **Accepted (owner)** | Storage key protection at rest (BRD 33.3). Owner chose plain text in the database. | Recorded as an owner-approved deviation; revisit before venue deployment (F-28). |
| S-14 | Medium → **Closed** | `eventStorageUrl` from an untrusted file could steer downloads/credential elsewhere; verifier crashed on a malformed port and accepted `?sig=`/`%2F`. | Fixed: exact-match verification, strict parse, tests. Phase 6–8 rule: never rebuild addresses from the file. |
| S-21 | Medium → **Closed** | The Azure lookup ignored the case-insensitive Event ID rule. | Fixed and tested. |
| S-22 | Medium → **Closed** | A key pasted into the container field was lower-cased into a valid-looking container name and **saved and shown**; a key pasted in the Event ID field was echoed in an error message. | Key-shaped text is refused in those fields and never echoed. |
| S-23 | Low → **Closed** | `.env`/environment fallback was not development-only, not validated, and mislabelled. | Fixed. |
| S-24 | Low → **Closed** | Read-only guarantee test was an evadable substring blacklist. | Replaced by AST guards (which are themselves tested against evasions). |
| S-25 | Low → **Closed** | Offline hang of ~13–37 s; Azure SDK request logging at INFO; bfcache could leave a form disabled; generic `read_blob`. | Fixed and tested. |
| S-26 | Info | Azure keys are full-power; "read-only" is code discipline (E9). | See F-28: prefer a read-only credential from the web-app team. |
| S-2, S-9 | — | Accepted admin credential (scope re-verified); single-instance lock (Phase 6/12). | Carried. |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context) | 21/09/26 | **Approved with notes** — no blockers; findings S-14 and S-21 – S-25 fixed; fixes not given a second independent pass |
| User (Vatsan) go-ahead | Vatsan | 21/09/26 | Approved (manual test passed) |
