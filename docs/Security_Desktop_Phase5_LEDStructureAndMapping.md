# Security Checklist — Desktop Phase 5: LED Structure & Device Mapping

Release/hand-off: Phase 5 — LED Structure & Device Mapping (Step 5.1 structure, Step 5.2 mapping, Step 5.3 connection testing). Date: 21/09/26. Application: Desktop.
Status values: **Pass**, **Fail**, **N/A at this stage**, **Accepted risk** (the BRD-accepted items and any the owner explicitly signs off).

**Theme of this phase:** the application now **writes to the local file system and to network shares** for the first time (a one-file probe), and takes **file paths typed by the operator**. Two things must stay true: a typed path can never make the application touch anything it should not, and no credential or path detail leaks into logs or pages.

## A. Identity & access (Web Application)

| # | Item | Status | Notes |
|---|---|---|---|
| A1–A6 | Web-application items | N/A at this stage | Web app only. |

## B. Identity & access (Desktop Application)

| # | Item | Status | Notes |
|---|---|---|---|
| B1 | Storage Account access key not committed and protected | **Accepted risk (owner, unchanged)** | Unchanged from Phase 4 (F-28). Phase 5 code never reads or handles the key (guard test: only `settings.py` mentions it). |
| B2 | Accepted-risk plain-text admin credential has not expanded in scope | **Accepted risk — scope unchanged** | The new modules touch neither `admin_*` nor `cloud_*` settings; no new use of `application_settings`. |
| B3 | Shared-folder credentials not logged | **Pass (by design: none exist)** | The application **stores, asks for and logs no shared-folder credential**. Access is whatever the running Windows account has. Tests scan the mapping, operation and exception tables for credential words. **Owner decision 21/09/26 (F-33): the venue devices are reached with the Windows login the application runs under, so no credential is ever stored.** |
| B4 (added) | Every new page and action is behind login and the launch cookie | **Pass** | `/events/<id>` and `/events/<id>/mappings` use `login_required`; tested signed-out and without the launch cookie (403). |

## C. Network & transport

| # | Item | Status | Notes |
|---|---|---|---|
| C1–C4 | Local server transport | N/A at this stage | Unchanged (loopback, exact Host, launch gate). New POST is CSRF-protected (tested: missing token → 403, nothing changes). |
| C5 | Outbound connections | **Pass** | Phase 5 adds none to the internet. The check talks only to the folder the operator typed (SMB via Windows). No ping, no sockets, no Azure, no other HTTP client (an import-level AST test; the Azure read-only AST guards still pass). |

## D. Data protection

| # | Item | Status | Notes |
|---|---|---|---|
| D3 | Typed values validated before use | **Pass** | IP: `ipaddress` parser (or blank). Folder: UNC `\\host\share[\sub]` or `X:\…` only; refuses relative paths, `.`/`..`, device paths (`\\?\`, `\\.\`), drive roots, wildcard/control/bidi characters, reserved device names, trailing dot/space, colon (NTFS alternate data streams), URLs, invisible/space-lookalike characters, over 200 characters. 35 refused forms, 9 more mid-path cases and 8 accepted forms (plus Arabic names) are tested. |
| D5 (added) | **The probe file cannot damage anything** | **Pass** | Created with exclusive create (`xb`): never overwrites or truncates (tested: an existing file of the same name is untouched); random 32-hex name with a recognisable prefix; deleted at once; a failed delete is reported as a harmless notice with the file name. Nothing is ever deleted except that one file. Tests confirm no debris after success and after failures. |
| D6 (added) | **Protected locations refused** | **Pass (after fix)** | The Windows folder, Program Files (both), ProgramData and **this application's own data folder** cannot be mapped (otherwise a probe or later file push could land next to the database or system files). The check runs on the **real location** (8.3 short names expanded, junctions and links followed) at save time and **again at every test**. Network paths to this computer (`localhost`, `127.x`, its own name) are refused because an admin share would reach any local folder. |
| D7 (added) | Duplicate destinations refused | **Pass (after fix)** | One folder cannot serve two LED destinations of an event — compared on the resolved path, ignoring case, so a short name or junction cannot disguise a duplicate; a hidden LED that returns after another destination took its folder comes back unmapped. Prevents one LED's assets overwriting another's in Phase 8. |

## E. Application security

| # | Item | Status | Notes |
|---|---|---|---|
| E1 | Input validation / injection | **Pass** | SQL parameterised. Event ID from the URL re-validated against the Event ID pattern; reserved IDs (`new`, `reregister`) cannot be registered. Form fields are read **only for LEDs the event actually enables**: forged fields for other tables/LEDs are ignored (tested). The action value is validated **before anything is saved**; a forged `test:` key is refused. |
| E2 | Output encoding / XSS | **Pass** | Jinja auto-escaping; `<script>` in a path and an attribute-breakout quote in a value are escaped (tested). No inline script/style (CSP unchanged). |
| E4 | CSRF | **Pass** | Token on the form; tested. |
| E5 | Errors do not leak internals | **Pass** | Failure messages are **fixed plain text** chosen from the Windows error *number* — never the Windows error text, the path, host name or user name (tested with hostile names). Unexpected exceptions inside a check become a generic message. |
| E8 (added) | The application does not hang on an unreachable device | **Pass** | A raw Windows call to an unroutable address blocks ~34 s (measured). Every check runs on a **daemon thread with a hard 10 s deadline**; Test All runs them in parallel under one deadline; a stuck thread cannot stop the application closing. |
| E9 | Read-only against Azure (Business Rule 13) | **Pass** | Unchanged and re-verified: the AST guards pass with the new modules; Phase 5 imports no Azure code. |

## F. Secrets management

| # | Item | Status | Notes |
|---|---|---|---|
| F1 | No secrets committed | **Pass** | `test_no_secrets` still passes; no new configuration values. |
| F3 | Logs do not print secrets or unnecessary paths | **Pass** | *Mapping Saved* names destinations ("Table 1 Inner"), not paths; *Device Test* rows carry the plain outcome; the shared-folder path appears only in the exception row for a failure (needed to fix it) and in the mapping table itself. |

## G. Logging, monitoring & audit

| # | Item | Status | Notes |
|---|---|---|---|
| G2 | Failed actions logged without sensitive data | **Pass** | Each failed test → one *exception_log* row (BRD §21 category: *Network / device connectivity*, *Permission*, *Missing folder*, *File access*, *Configuration*) with event, table, LED, destination — no Windows error text. Rejected forms are operator typos and are **not** logged as exceptions. |
| G4 | Audit completeness and atomicity | **Pass** | Saving a mapping and its audit row are one transaction (all-or-nothing across all rows — one bad row saves nothing). A test result, its audit row and any exception row are one transaction. Registration/re-registration creates the mapping rows inside the same transaction as the event. |

## H. Dependency & platform

| # | Item | Status | Notes |
|---|---|---|---|
| H1 | No new dependencies | **Pass** | Standard library only (`os`, `ipaddress`, `threading`, `pathlib`). Runtime closure unchanged (24 packages). |
| H2 | Supported runtime | Pass | Python 3.11.9. |

## Findings

| ID | Severity | Finding | Action |
|---|---|---|---|
| S-27 | Low → **Closed** | A colon inside a folder name (`\\d\a:stream`) passed validation; on NTFS it names an alternate data stream. | Refused; tested. |
| S-28 | Info → **Closed** | "Network path not found" (Windows 53) is raised by Python as *FileNotFoundError* and was reported as "folder missing" instead of "device unreachable". | Classification now uses the Windows error number first; regression-tested. |
| S-29 | Low → **Closed** | Pressing Enter in a text box would have run the first row's **Test** instead of **Save**. | A default Save button is first in the form; tested. |
| S-32 | Medium → **Closed** | (Review) Protected-folder and data-folder checks bypassed by 8.3 short names, loopback admin shares and junctions (the probe wrote through a junction). | Real-location check at save and at test; loopback/self hosts refused; regression-tested. |
| S-33 | Medium → **Closed** | (Review) A returning hidden mapping could duplicate an enabled destination's folder. | Cleared on return when it would conflict; tested. |
| S-34 | Medium → **Closed** | (Review) A test result could be written onto a folder changed while the 10-second test ran, showing an untested folder as *Connection Successful*. | Result applied only if the row still holds the tested folder; otherwise the operator is told to re-run; tested. |
| S-35 | Low → **Closed** | (Review) A database error while saving a result gave an error page and lost the results; invisible/bidi/space-lookalike characters, `CON .txt`, `COM` + superscript digits and 200+-character paths were accepted; a removed-probe warning was not audited. | Plain message; stricter character rules (Arabic names still allowed); limit lowered to 200; warning audited. |
| S-36 | Info | Any host is accepted as a network target, so testing a hostile UNC address would send this Windows account's credentials to it (operator-trust point); remote-share junctions cannot be resolved from here (QA F-35). | Accepted; operators enter the venue's own devices. |
| S-30 | Low | The test proves access for **this** Windows account only (F-32). Owner decision 21/09/26: devices are reached with the Windows login the application runs under (F-33), so no credential is stored. | Closed — decided; revisit F-32 for unattended runs (Phase 12). |
| S-31 | Low | The probe is written into the real destination folder (F-34). | Closed — owner accepted 21/09/26. |
| S-2, S-4, S-9 | — | Accepted admin credential (scope re-verified); accepted plain-text key (F-28); single-instance lock (Phase 6/12). | Carried. |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context) | 21/09/26 | **Approved with notes** — no blockers; findings S-32 – S-35 fixed and regression-tested; fixes not given a second independent pass |
| User (Vatsan) go-ahead | Vatsan | 21/09/26 | Approved (manual test passed) |
