# LED Asset Download and Synchronisation Application — Workflow / Progress Tracker

**This is a living document.** Claude Code must amend this same file after every phase is completed (not create a new file) — updating status, completion date, what was actually built, and a link to that phase's QA Test Case and Security Checklist documents.

Status values: `Not started` / `In progress` / `Awaiting user go-ahead` / `Complete`.

---

## Phase 0 — Project Setup
- **Status:** Complete (user go-ahead 20 September 2026; committed `a35cfe2` and pushed to `main`)
- **Completed on:** 20 September 2026
- **What was built:**
  - **Step 0.1:** standalone git repo in `Applications/desktop` (`origin` = `desktopsportspres`, branch `phase-0-project-setup`; `.gitignore` written before anything else); Python 3.11 venv; package `ledsync/` split into `web/`, `services/`, `db/`, `config.py` per BRD §5. Flask served on a loopback-only random port in a background thread and shown in a native pywebview (Edge WebView2) window; WTT-branded placeholder (black surface, Roboto Bold headline, one orange accent, brand tokens as CSS variables). Clean shutdown on window close (verified: no leftover process). Startup failures show a dialog and write `<data>\logs\ledsync.log`; WebView2 absence is detected up front. `--auto-close N` flag for smoke tests.
  - **Step 0.2:** SQLite DB with the seven §26 tables (`ledsync/db/schema.py`), idempotent creation, existing data never touched, schema version via `PRAGMA user_version`, newer-version DBs refused, schema drift detected at startup; WAL + 5 s busy timeout + foreign keys; per-request connection helper.
  - Security groundwork: `Host`-header check (fails closed), security headers/CSP, per-launch `SECRET_KEY`, `tests/test_no_secrets.py` guard, `pip-audit` clean on the 15-package runtime closure.
  - 41 automated tests passing; real-window launch/close verified by script (screenshot `docs/screenshots/phase0-blank-window.png`).
- **QA Test Case doc:** `docs/QA_Desktop_Phase0_ProjectSetup.md` (41 cases: 40 pass, 1 not run — TC-24, true clean machine, needs the Phase 13 installer)
- **Security Checklist:** `docs/Security_Desktop_Phase0_ProjectSetup.md`
- **Independent architect review:** Approved with notes, no blockers. Fixed now: WebView2 pre-check, startup-failure visibility (dialog + log file), lazy `webview` import and guarded `__main__`, WAL/busy-timeout/per-request DB, Host check fails closed + per-launch secret key + tighter CSP, `*.pfx/*.pem/*.key` ignored, tests added for gaps. Carried forward: per-launch token/CSRF (Phase 1), headless entry point + single-instance lock + data-dir/account decision (Phase 8/12), migration runner (Phase 3/13).
- **Deviations:**
  1. **Schema extended beyond BRD §26 with owner approval (20 Sep 2026, "Extend now"):** `events.configuration_json` (BRD §7.1) and five `exception_log` columns — `table_number`, `led_type`, `file_name`, `source`, `destination` (BRD §21.1). All marked `-- EXT` in `schema.py`. Also added, not in §26: `UNIQUE(event_id, table_number, led_type)` on `led_mappings` and foreign keys from `led_mappings`/`download_history`/`sync_history` to `events` (logs deliberately have none, so a rejected registration can be logged).
  2. **`ralph-loop` not run** for this phase: Phase 0 has no real UI to polish. `wtt-brand` was applied to the placeholder window. Will run from Phase 1 — user to confirm.
  3. **Reviewer's proposed WebView2 fix (`gui="edgechromium"`) was not used:** pywebview's own source shows it still falls back silently to MSHTML when the runtime is missing, so the app checks the registry itself and refuses to start instead.
  4. **Font files (Roboto Bold, Bio Sans) not bundled** — app must run offline; falls back to system fonts. Bio Sans files needed from the brand owner.
  5. **"Clean machine" validation** only approximated (fresh venv from `requirements.txt`) until the installer exists.
- **Security finding S-1 — resolved 20 September 2026:** a live Storage Account key sat in plaintext in the parent repo's `references/0forimplementation/desktop/OBSERVATIONS.txt` (never committed). On the owner's instruction that file is now git-ignored in the parent repo (`.gitignore` edited; parent repo not committed by Claude). Owner has not (yet) decided on key rotation. The file's contents were not modified.

## Phase 1 — Login
- **Status:** Complete (owner's manual test passed and go-ahead given 21 September 2026; built and reviewed 20 September; committed `47e3461`, merged to `main` `47ca4f7`; logo addition committed on 21 September — see table)
- **Completed on:** 21 September 2026
- **What was built:**
  - **Step 1.1:** WTT-branded Sign In screen (username autofocus, Show/Hide password toggle, single orange `SIGN IN`), placeholder Dashboard ("Signed in as admin", Change Password, Sign Out, full-width "No Events Yet" panel), Change Password screen. Fixed administrator `admin` / `Admin@123` seeded on first run into `application_settings` **in plain text (BRD 6.1 accepted risk)**; password changeable (current password + ≥ 8 chars + confirmation); a changed password survives restarts/upgrades (seed never overwrites).
  - **Owner decisions applied (20 Sep 2026):** credential lives in `application_settings`; only the password is changeable; short throttle (5 failures → 30 s); no idle timeout; no forced first-login change.
  - **Security (closes Phase 0's carried F-06):** loopback server gated by a one-time launch token exchanged for a signed HttpOnly SameSite=Strict session cookie; CSRF token on every POST; session fixation defence; server-side session epoch so logout and password change revoke previously issued cookies; atomic throttle; exact `127.0.0.1:<port>` Host only; branded 403/404/405/413 pages; extra security headers; no server version advertised; WebView2 private mode; launch token never logged; Werkzeug access log suppressed.
  - **Logging:** login success/failure/blocked, logout, password change and application startup written to `operation_log` (UTC ISO timestamps, never any credentials; audit-write failures never break a login).
  - **Window:** now opens maximised (a fixed 800px-high window overshot the 752px working area on this 1280×800 display).
  - 117 automated tests pass (Phase 0 + 1); real-window keystroke run captured screenshots `docs/screenshots/phase1-*.png`.
- **Owner follow-up (21 Sep 2026):** added the WTT logo (`reference-docs/WTT-Logo.png` → `ledsync/web/static/img/wtt-logo.png`) to the Sign In screen, the signed-in header (with the app name beside it) and the error page.
- **QA Test Case doc:** `docs/QA_Desktop_Phase1_Login.md` — 65 cases: 63 pass (48 automated, 4 real-window, 11 manual run by the owner), 2 manual not run (keyboard tab order, diagnostic-log check); 119 automated tests pass.
- **Security Checklist:** `docs/Security_Desktop_Phase1_Login.md` (B2 scope guard verified by test: only `services/auth.py` touches the credential)
- **Independent architect review:** Approved with notes, no blockers. Fixed now: session revocation (demonstrated by cookie replay), atomic throttle, branded dead-end 403, `localhost` removed from Host list, compare-and-set password update, best-effort audit writes, extra headers, accessibility (`aria-describedby`, toggle semantics), explicit private mode. Carried forward (QA doc F-12 / Security S-9): single-instance lock, settings service that excludes `admin_*`, log display/retention, migration runner + UI-free bootstrap, data-dir vs headless decision.
- **`ralph-loop` / `wtt-brand` polish:** run (2 of max 3 iterations used; completion promise met). Changes: orange now used only for the primary button (removed decorative bar/dot), hover colour tokenised, input styling made type-agnostic; enforced by a test.
- **Deviations:**
  1. Added non-brand functional tokens (`--status-error`, `--status-ok`, neutral surface/border greys) because the WTT palette defines no error/success colour or control borders; borders are deliberately strong for contrast on black (the owner's web-app feedback).
  2. `operation_log` timestamps stored as UTC ISO; display conversion to DD/MM/YY HH:MM:SS is a later-phase task. This fixes UTC for this log only and does **not** settle the open BRD §36 timezone decision for change-log comparison.
  3. Static CSS/JS is exempt from the launch gate (public source) so the branded error page can render.
  4. Fonts (Roboto Bold/Bio Sans) still not bundled — Bio Sans files needed from the brand owner (QA F-03).

## Phase 2 — Event Dashboard (SQLite only)
- **Status:** Complete (owner's manual test passed and go-ahead given 21 September 2026; built and independently reviewed the same day; merged to `main` and pushed — hashes in the table below)
- **Completed on:** 21 September 2026
- **What was built:**
  - **Step 2.1:** the Dashboard now lists registered events from the local `events` table: a full-width, high-contrast table with **Event ID, Event Name, Last Updated, Status**, an "N registered" count, and a clean empty state ("No Events Registered Yet"). Most recently updated first; undated rows last. Times are `DD/MM/YY HH:MM` (24-hour) in the venue machine's **local time** (stored as UTC ISO). Status is a text badge.
  - **Owner decisions applied (20 Sep 2026):** local-time display; status vocabulary **Registered / Ready / Synced / Attention needed** (blank = Registered; unknown text shown as-is with a neutral badge).
  - **Robustness:** display code can never raise on stored data. Only dates 1970–2098 are converted (Windows cannot convert others); anything else, and any unparseable value, is shown as its raw text; invalid-UTF-8 text, BLOBs and numeric values are coerced; each row is isolated so one bad row cannot hide the list; a branded 500 page replaces the stock server error page.
  - **Dev helper** `scripts/insert_test_event.py` (not shipped): inserts sample rows only into an explicitly named scratch folder; refuses the real data folder and non-existent folders (unless `--create`); never overwrites an existing event.
  - 207 automated tests pass (Phases 0–2). Populated and empty states were rendered from the real templates and viewed in Chrome (`docs/screenshots/phase2-*.jpg`).
- **QA Test Case doc:** `docs/QA_Desktop_Phase2_EventDashboard.md` — 43 cases: 34 pass (31 automated, 3 rendered-page), 9 manual pending the owner's run; includes the step-by-step real-app guide.
- **Security Checklist:** `docs/Security_Desktop_Phase2_EventDashboard.md` (B2 scope guard re-verified: `events.py` never touches `application_settings`)
- **Independent architect review:** **First pass REJECTED** — reproduced BLOCKER: one row with a timestamp before 1970 (or year 9999 etc.) made the whole dashboard return HTTP 500 on Windows. Fixed, along with: tests that had missed it, a dev script that could have overwritten a real event, missing raw values on rows, BLOB/NUL handling, and table keyboard access. **Focused re-check: APPROVED with notes** (it found one more hole — invalid-UTF-8 text — now fixed, and the year-2099 display edge, now fixed by accepting only 1970–2098).
- **`ralph-loop` / `wtt-brand`:** brand rules applied (orange untouched; blue used as the Ready badge border with white text for contrast; strong borders). `ralph-loop` was **not re-run** for this phase: it is one table screen with no new interaction beyond what Phase 1's loop covered — say if you want a pass.
- **Deviations:**
  1. Event status vocabulary differs from BRD 33.4's operation-status wording, by owner choice; later phases and the Phase 11 email must map between them. "Ready" is defined as configured and mapped (Phase 5/8 to confirm exactly when it is set).
  2. The native-window keystroke test could not log in reliably on this machine (a Windows voice-typing overlay interferes with focus), so the populated table was verified from the app's real templates in Chrome instead. Browser password entry was deliberately not performed.
  3. Date-only stored values (`2026-09-08`) are treated as UTC midnight, so west of UTC they show as the previous day. Application-written values always carry a time.

## Phase 3 — Event Registration and GUID Validation
- **Status:** Complete (owner's manual test passed and go-ahead given 21 September 2026; built, polished and independently reviewed the same day; merged to `main` and pushed — hashes in the table below)
- **Completed on:** 21 September 2026
- **What was built:**
  - **Step 3.1 — Add New Event:** an orange **ADD NEW EVENT** button on the Dashboard opens a form (Event ID + file picker). The chosen `_GUID.json` is strictly parsed and validated (BRD 10): JSON well-formed with no duplicate keys/NaN; `eventId` equals the ID typed; `eventName`; https `eventStorageUrl`; valid non-nil `exportGuid`; ≥1 table with unique numbers and Inner/Outer/Main booleans (safety cap 100 tables). A valid file creates the `events` row (ID, name, lower-case GUID, source, `configuration_json`, status **Registered**, Last Updated = the file's export time, normalised to UTC).
  - **Step 3.2 — GUID validation (BRD 9.2):** first registration checks the GUID is well-formed and not owned by another event; an already-registered event compares recorded vs file GUID — match = harmless notice, mismatch = **rejected and logged** to the new minimal **exception log** (BRD 21/21.1, all ten categories defined). Typos are shown but not logged.
  - **Re-registration (owner design):** on a mismatch the operator is redirected to **Re-Register Event** and must paste the new GUID copied from the web application, which must equal the GUID in the newly supplied file; the file's GUID is shown only by its last 6 characters so it cannot be copied back. A wrong paste is refused and logged; Cancel keeps the old registration. The rejected file is held server-side briefly (15 min, single use, bounded) and the form is bound to that exact request.
  - **Audit:** registration and re-registration are written to `operation_log` in the same transaction as the change, with the GUID(s) and source (re-registration: `old -> new`).
  - **Test data:** nine scenario files with a README in `docs/testfiles/phase3/`.
  - 418 automated tests pass (Phases 0–3; 211 new). Screens rendered from the real templates and viewed in Chrome (`docs/screenshots/phase3-*.jpg`).
- **QA Test Case doc:** `docs/QA_Desktop_Phase3_EventRegistration.md` — 56 cases: all 56 pass (38 automated groups, 3 rendered-page, 15 manual run by the owner: "All ok"); includes the step-by-step real-app guide.
- **Security Checklist:** `docs/Security_Desktop_Phase3_EventRegistration.md` (B2 scope guard re-verified: registration code never touches `application_settings`)
- **Independent architect review:** **Approved with notes**, no blockers (ran the suite and probed the running app). Six "fix now" findings resolved: lone-surrogate crash (500, unlogged), spoofing/bidi characters accepted, the re-register page displaying the GUID the operator was meant to obtain independently, the re-register form not bound to its page (two-tab confusion), audit rows lacking GUIDs, and event/audit rows not committed atomically. Cheap notes also done: timestamp normalisation, re-registration clears `last_download`/`last_sync`, error focus/`aria-describedby`. **Carried forward:** Phase 4 host allow-list for `eventStorageUrl` and Event-ID case rule; UNIQUE index on GUID (with the migration runner); widen the rejection logger at Phase 8. The fixes were verified by 45 new tests but did **not** get a second independent review pass.
- **`ralph-loop` / `wtt-brand`:** run (2 of max 2 iterations; completion promise met). Change: the re-register screen widened so GUIDs stay on one line (later superseded by hiding the file GUID). Brand rules held: orange only on the single primary action per screen.
- **Deviations:**
  1. **Last Updated at registration = export time from `_GUID.json`** (owner choice, 21 Sep 2026) — differs from BRD 7.2's “latest relevant asset update”; Phase 6 will overwrite it from the change log.
  2. **Re-registration flow is stricter than BRD 9.2** (owner design): the operator must paste the new GUID from the web application; the file's GUID is deliberately not displayed in full.
  3. **File picker upload instead of a typed path** (owner choice); the Implementation Sequence says “point it at a local file”.
  4. **Re-registration clears `last_download`/`last_sync` and resets status to Registered** (nothing has come from the new export). Device mappings are left untouched — whether to keep/clear/re-validate them is raised at Step 5.2.
  5. A technical 100-table cap protects against hostile files; it is **not** the open BRD §36 “maximum tables” decision.
  6. Browser password entry was again not performed; screens verified from rendered pages, and the owner's manual run in the real window is the true check.

## Phase 4 — Azure Storage Connectivity
- **Status:** Complete (owner's manual test passed and go-ahead given 21 September 2026; built, polished, independently reviewed and validated against live Azure the same day; merged to `main` and pushed — hashes in the table below)
- **Includes:** first live connection to the (confirmed) Storage Account using the access key.
- **Completed on:** 21 September 2026
- **What was built:**
  - **Step 4.1 — Cloud Storage settings** (header link **Settings**): storage account, default year container and access key; saved to `application_settings`, reloaded after restart; the key is never shown again after saving (empty field, green **Saved** badge), never logged, never in an error, and reachable only through `services/settings.py` (pages get a key-less view). **Test Connection** only lists containers, uses a typed or saved key, saves nothing and says so. Event storage path is taken from each event's own `eventStorageUrl`; there is no Asset storage path setting (files are located from change-log relative paths) — both explained on the screen.
  - **Step 4.2 — live registration:** Add New Event takes only the Event ID; the app finds `<EventID> - …` in the default year container then the other year containers (newest first), downloads `_GUID.json` (read-only, size-capped), validates it exactly as in Phase 3, **verifies the storage address inside the file equals the account/container/folder it came from (S-14)**, and registers. Re-registration works unchanged through Azure. The file picker is gone (cloud only).
  - **Read-only Azure client:** `services/storage.py` lists and downloads only, with bounded timeouts/retry (an offline venue fails in ~2–15 s), errors mapped to BRD §21 categories with no SDK text; enforced by AST-based tests (no mutating/SAS/private-SDK/dynamic/other-HTTP-client code anywhere).
  - **Event IDs are case-insensitive** (Azure lookup, registration, NOCASE unique index; leading zeros significant).
  - **Development aids:** `.env`/environment fallback (development only; ignored by an installed build; validated), `scripts/set_storage_key.ps1` (masked entry, refuses unless git-ignored, ACL-restricted), opt-in `--live` tests.
  - **Validated against the real account (read-only):** connect and list containers; find `1000 - Star contender Doha` from just `1000`; download and fully validate the real `_GUID.json` and its address; unknown event, wrong key and wrong account handled cleanly; **Event 1000 registered end to end through the real app code** with every field stored and no credential anywhere in the local database. 7 live tests pass.
  - 649 automated tests pass (Phases 0–4; 231 new) plus 7 live.
- **QA Test Case doc:** `docs/QA_Desktop_Phase4_AzureConnectivity.md` — 66 cases: all 66 pass (42 automated groups, 7 live, 1 rendered-page, 16 manual run by the owner: "All ok"); includes the step-by-step real-app guide (incl. hiding `.env` to prove the Settings screen).
- **Security Checklist:** `docs/Security_Desktop_Phase4_AzureConnectivity.md`
- **Independent architect review:** **Approved with notes**, no blockers; it tried to leak the key on every path and could not. Ten fix-now findings resolved: crash (HTTP 500) and looseness in address verification (`:abc` port; `?sig=`, `%2F`, fragments accepted), the Azure lookup ignoring the case rule, Test Connection silently not saving a typed key, the fallback not being development-only or validated, keys pasted into the wrong field being echoed (and one being saved as a container name), offline hang (~13–37 s → ~2 s), the evadable substring read-only test (now AST), bfcache leaving a form disabled, Azure SDK request logging, and a generic `read_blob`. Also done: key-less settings view, real `raise` instead of `assert`, fullmatch year containers, base64 key validation, proxy/sign-in-page and empty-blob handling, Flask-free storage factory. **Carried forward:** persist verified container/folder and blob properties (Phase 6–7); packaging certifi/cffi and proxy/TLS behaviour (Phase 13). The fixes were verified by 81 new tests and re-validated live, but did **not** get a second independent review pass.
- **`ralph-loop` / `wtt-brand`:** run (1 of max 2 iterations; completion promise met). Change: the settings summary is no longer a box inside a box; orange still only on the single primary action per screen.
- **Deviations:**
  1. **Access key stored as PLAIN TEXT in the database — owner decision (21 Sep 2026), an explicit departure from BRD 33.3.** Recorded as an owner-approved accepted risk (Security B1/F2, QA F-28). Azure keys are full-power (they can write), so "read-only" is a rule the application keeps, not something the key enforces. Recommended before venue deployment: a read-only container-scoped credential and/or Windows encryption (single seam in `settings.py`).
  2. **No editable "Event storage path" / "Asset storage path" settings** (BRD 14 lists them): the event path comes from each event's `eventStorageUrl`; assets are located from change-log relative paths (owner's reasoning, 21 Sep 2026).
  3. **A `.env`/environment fallback supplies unsaved settings in development** (not in the BRD); a saved value always wins, and an installed build ignores it.
  4. **New dependency** `azure-storage-blob` 12.30.2 (24 packages; no known vulnerabilities).
  5. **Real cloud differs from the BRD (found during live validation; decisions needed at Phase 5/6):** change log is `_ledassetschangelog.csv` (with an "s"), not `_ledassetchangelog.csv`; cloud folders are lowercase (`Table 1/inner|outer|mainled`) while change-log entries use `Inner/Outer/MainLED` (contradicting Addendum A §39.1); `keepalive.txt` placeholders and a file with no extension exist in the folders.
  6. Native-window keystroke automation is unreliable on this machine and a password was never typed into a browser; screens verified from rendered pages, and the owner's manual run is the true check.

## Phase 5 — LED Structure and Device Mapping
- **Status:** Not started
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

## Phase 6 — Change Log and Incremental Download Logic
- **Status:** Not started
- **Includes:** full-path matching for `_ledassetchangelog.csv` entries (v2.4 note — multiple `sponsorsequence.csv` per event).
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

## Phase 7 — Asset Download
- **Status:** Not started
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

## Phase 8 — Push / Synchronise to LED Devices
- **Status:** Not started
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

## Phase 9 — Error Handling, Exception Log, Application Log
- **Status:** Not started
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

## Phase 10 — Application Settings and Dashboard Polish
- **Status:** Not started
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

## Phase 11 — Email Notification
- **Status:** Not started
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

## Phase 12 — Scheduled Operation
- **Status:** Not started
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

## Phase 13 — Installer and Upgrade Handling
- **Status:** Not started
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

## Phase 14 — Full Workflow and Acceptance Validation
- **Status:** Not started
- **Completed on:** / **What was built:** / **QA Test Case doc:** / **Security Checklist:** / **Deviations:**

---

## Overall status

| Phase | Status | Go-ahead date | GitHub commit |
|---|---|---|---|
| 0 | Complete | 20 Sep 2026 | `a35cfe2` |
| 1 | Complete | 21 Sep 2026 | `47e3461` (merge `47ca4f7`); logo follow-up `8ffc002` |
| 2 | Complete | 21 Sep 2026 | `29e54c5` + `ac03ded` (merge `8d2c25e`) |
| 3 | Complete | 21 Sep 2026 | `8cb2c81` + `1369bac` (merge `6405679`) |
| 4 | Complete | 21 Sep 2026 | `cdc9aa8`, `35bb338`, `f03586e` (merge `2efaafe`) |
| 5 | Not started | | |
| 6 | Not started | | |
| 7 | Not started | | |
| 8 | Not started | | |
| 9 | Not started | | |
| 10 | Not started | | |
| 11 | Not started | | |
| 12 | Not started | | |
| 13 | Not started | | |
| 14 | Not started | | |
