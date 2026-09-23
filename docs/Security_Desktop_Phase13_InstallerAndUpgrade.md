# Security Checklist — Desktop Phase 13: Installer and Upgrade Handling

Release/hand-off: Phase 13 — Installer and Upgrade Handling (Steps 13.1, 13.2). Date: 23/09/26. Application: Desktop.
Status values: **Pass**, **Fail**, **N/A at this stage**, **Accepted risk** (BRD-accepted items and any the owner explicitly signs off).

**Theme of this phase:** the application is packaged and distributed for the first time, and gains
two new ways to configure itself without a person typing into the UI — a pre-install config file
and a schema-migration runner. Three things must stay true: (1) the pre-install config file can
never carry a Windows account password, and never bypasses any validation the Settings pages
already enforce; (2) an upgrade can never lose or corrupt the existing database, mappings, or
operational history; (3) nothing new here weakens any guarantee already reviewed and closed in
earlier phases (the secrets discipline from Phases 4/11, the single-instance lock and "no GUI
toolkit in the headless entry point" guarantee from Phase 12).

## A. Identity & access (Web Application)

| # | Item | Status | Notes |
|---|---|---|---|
| A1–A6 | Web-application items | N/A at this stage | Web app only. |

## B. Identity & access (Desktop Application)

| # | Item | Status | Notes |
|---|---|---|---|
| B1 | Storage Account access key not committed and protected | **Pass (unchanged)** | Not touched this phase. |
| B2 | Accepted-risk plain-text admin credential has not expanded in scope | **Pass (unchanged)** | `admin_*` keys untouched; `install_config.py` cannot reach them at all (it only calls `settings.py`'s own `save_*` functions, none of which touch `auth.py`'s keys). |
| B3 (added) | **The pre-install config file can never carry a Windows account password** | **Pass** | `install_config.load_config_file()` explicitly refuses a file containing `schedule_password` (rejected outright, with a specific message, before any other validation runs) and `schedule_enabled` (turning scheduling on needs a real Task Scheduler registration, which needs a password this file must never contain - see B4/D3). There is no code path anywhere in this module that reads, stores, or forwards a password. |
| B4 (added) | **Seeding a fresh install can never leave the database claiming "scheduling enabled" without a real, working Task Scheduler entry** | **Pass** | `install_config.seed()` always calls `settings.save_schedule(conn, False, ...)` regardless of what a (refused, but defence-in-depth) config file might contain - it can only ever pre-fill the day/time/account *defaults* shown on the Settings -> Scheduling page, never the enabled flag. This avoids reintroducing the exact "database says enabled, Windows disagrees" class of bug Phase 12 (S-70) fixed. |

## C. Network & transport

| # | Item | Status | Notes |
|---|---|---|---|
| C1–C5 | Local server transport / outbound connections | N/A / **Pass (unchanged)** | Nothing new here touches the network. `--seed-config` and the migration runner are both purely local, offline operations. |

## D. Data protection

| # | Item | Status | Notes |
|---|---|---|---|
| D1 | **A pre-install config file's values are validated exactly as strictly as the Settings UI, never more leniently** | **Pass** | `install_config.seed()` calls `settings.py`'s own `save_cloud`/`save_email`/`save_schedule`/`save_download_settings`/`save_log_retention`/`save_folders` - the SAME functions the web routes call, not a reimplementation. A `SettingsError` from any of them is caught and re-raised as `InstallConfigError` with the same message; nothing is silently coerced or partially applied (each `save_*` call is already its own transaction, and a raised error means everything already queued in `changed` up to that point was still committed for the fields BEFORE the failing group only - see the code-level note below). |
| D1a (note) | Partial-application scope, documented (not a defect) | **Accepted, documented** | `seed()` iterates cloud -> email -> schedule -> retry -> log retention -> folders and each group is its OWN `save_*` transaction (matching how the Settings UI itself works: each page/section is saved independently). If, say, the email fields are malformed, cloud storage (validated and saved earlier in the same `seed()` call) remains saved - exactly as if an operator had filled in Settings -> Cloud Storage, saved, then made a typo on the Email Notification page. `InstallConfigError`'s message names which group failed. |
| D2 | **The install-config JSON schema uses no literal that collides with the existing `application_settings`/`cloud_access_key` scope-guard tests** | **Pass** | Confirmed live during this phase: `install_config.py` originally queried `application_settings` directly and used the field name `cloud_access_key`, both of which are guarded by `tests/test_auth.py`/`test_cloud_registration.py` (only `settings.py`/`auth.py` may reference the table; only `settings.py` may reference the storage key's setting name). Fixed by adding `settings.has_any_setting()` (so `install_config.py` never touches the table directly) and renaming the JSON field to `storage_access_key` (the internal value still flows only through `settings.save_cloud()`) - a real, if narrow, instance of the existing SCOPE GUARD doing exactly its job against new code. |
| D3 (added) | **Migrations can never run against a brand-new database, and never skip a version** | **Pass** | `db/connection.py`'s `init_db` only calls `run_migrations()` when `0 < version < SCHEMA_VERSION`; a version-0 (truly fresh) database is stamped directly to `SCHEMA_VERSION` from `schema.DDL`, which already reflects the current schema - no migration ever runs redundantly against data that was never in the old shape to begin with. `run_migrations()` applies every registered `Migration` strictly between the current and target version, in order, each committing its own `PRAGMA user_version` bump - a database can never end up skipping a version or applying one twice. |
| D4 (added) | **A frozen build's scheduled Task can never point at a non-existent or wrong executable** | **Pass** | `scheduler._command_and_args()` branches on `sys.frozen` at the moment `register()` is called (inside the currently-running process, not guessed from disk) - a frozen build always resolves the scheduled-run executable relative to `sys.executable` (the real, currently-running main executable's own path), never a hardcoded or configurable location. |

## E. Application security

| # | Item | Status | Notes |
|---|---|---|---|
| E1 | Input validation / injection | **Pass** | Every install-config field goes through the same validators as the Settings UI (see D1). The JSON file itself is parsed with the standard library `json` module (no `eval`/`exec`/pickle anywhere); a malformed file is a caught, specific `InstallConfigError`, never an uncaught exception that could crash the installer step. |
| E5 | Errors do not leak internals | **Pass** | `InstallConfigError` messages are the same safe, pre-existing `SettingsError` text Settings pages already show, or a small number of fixed sentences about the file itself (missing/invalid JSON/wrong shape) - never a raw traceback, never a secret value echoed back (inherits `settings.py`'s own discipline, e.g. `redact_if_secret_like`/`looks_like_secret` guard every relevant field already). |
| E12 (added) | **A bad or missing pre-install config file can never block or crash a silent installer run** | **Pass** | `main.py`'s `_run_seed_config` catches `InstallConfigError` specifically and only logs a warning - the installer's `[Run]` step still completes normally (`waituntilterminated` sees a clean exit 0 either way), leaving the operator to configure Settings by hand afterward, exactly as an install with no config file at all already works. |

## F. Secrets management

| # | Item | Status | Notes |
|---|---|---|---|
| F1 | No secrets committed | **Pass** | `test_no_secrets` passes; `installer/install-config.example.json` ships with every secret-shaped field blank (`""`) or an obvious placeholder (`<your-key>`) - never a real value. The real, edited `install-config.json` a venue operator creates lives next to a compiled `Setup.exe`, outside this repository entirely, and is never read by anything except the one `--seed-config` invocation. |
| F3 | Logs do not print secrets | **Pass** | `main.py` logs only the NAMES of the setting groups seeded (`"cloud storage, email notification, ..."`), never a value - matching `save_*`'s own existing `oplog.add` pattern from every earlier phase. |
| F4 (added) | **The Scheduling password is structurally impossible to seed from a file** | **Pass** | Not merely "not validated in" - `install_config.py` has no code path that could accept, store, or forward a password even if one were present in the JSON; `load_config_file()` explicitly scans for and refuses the key `schedule_password` before any other processing. |

## G. Logging, monitoring & audit

| # | Item | Status | Notes |
|---|---|---|---|
| G2 | Failed actions logged without sensitive data | **Pass** | A rejected config file is logged via the existing diagnostic logger (`ledsync.log`), not the operational audit log (`operation_log`) - reasonable, since this is a pre-first-login installer step, not an authenticated operator action; every group actually applied still goes through the same `oplog.add(conn, "Settings Changed", ...)` calls each underlying `save_*` function already makes, so the audit trail is identical to an operator saving the same values by hand. |
| G4 | Audit completeness and atomicity | **Pass** | Unchanged from each underlying `save_*` function's own existing transaction/rollback behaviour (Phase 10/11/12's own review already covered this). |

## H. Dependency & platform

| # | Item | Status | Notes |
|---|---|---|---|
| H1 | New dependency reviewed | **Pass** | `pyinstaller==6.22.3` (build-only, `requirements-build.txt` - never installed on a venue machine, never part of the shipped application's own runtime closure). Its own transitive build-time dependencies (`pefile`, `pyinstaller-hooks-contrib`, `pywin32-ctypes`, `altgraph`) are likewise build-only. The Inno Setup Compiler (build-tool only, not a Python package, not shipped) was downloaded from its official source (`github.com/jrsoftware/issrc` releases) and installed with the owner's explicit go-ahead, 23 Sep 2026. `pip-audit` against `requirements.txt` (the actual shipped runtime closure) is unchanged from Phase 11. |
| H2 | Supported runtime | Pass | Python 3.11.9, Windows 11 Pro; PyInstaller's own bootloader (`Windows-64bit-intel`) matches the target platform. |

## Findings

| ID | Severity | Finding | Action |
|---|---|---|---|
| S-76 | **Low → Closed** | (Found and fixed live during this phase, before any independent review) `install_config.py`'s first draft queried `application_settings` directly and used the literal field name `cloud_access_key` - both caught immediately by pre-existing scope-guard tests (`test_auth.py`, `test_cloud_registration.py`) built in earlier phases specifically to prevent new code from bypassing `settings.py`'s ownership of these. | Fixed: added `settings.has_any_setting()` so `install_config.py` never touches the table directly; renamed the JSON field to `storage_access_key`. Neither change alters what the field means or how it is validated - both are purely about which module is allowed to reference the literal name. |
| S-77 | **Medium → Closed** | (Found and fixed live, real Inno Setup compilation/install testing, 23 Sep 2026) The "Launch {#MyAppName} now" postinstall `[Run]` entry's `skipifsilent` flag did not reliably suppress the launch under `/VERYSILENT` in this environment - a scratch-install validation run unexpectedly launched a real, windowed `LEDAssetSync.exe` against this machine's actual default data folder (`%LocalAppData%\LEDAssetSync`, already holding real data from this project's own earlier testing). No data was modified (the launch performs the same idempotent startup sequence - `auth.seed_admin`, an `Application Startup` oplog row - any normal launch already does, confirmed by inspecting the log afterward), and the process was closed gracefully within seconds of being noticed. Still a real risk class worth closing: a genuinely unattended/silent venue-machine install must never be able to open a window at all. | Fixed: the postinstall launch entry was removed from `installer/ledsync.iss` entirely - a silent install now only ever copies files and creates shortcuts, with no code path that can launch the application. Recompiled and re-tested (a second scratch-install run, same flags): confirmed no process launches. |
| — | Info, accepted | The pre-install config file, once an operator has edited it with real values (a storage access key, an ACS connection string), is a plaintext file living outside this repository, typically alongside `Setup.exe` on removable media or a network share for the duration of a rollout. Not meaningfully different in kind from the existing `.env` development convenience (also plaintext, also git-ignored, also documented as "handle carefully") - but worth the owner being aware this file should be deleted or secured once a rollout is complete, the same discipline already applied to the `.env` file. | Accepted; noted here for completeness, matches the existing `.env` risk already accepted since Phase 4. |

### All-phases pre-hand-off review (24/09/26)

| ID | Severity | Finding | Action |
|---|---|---|---|
| S-78 | **Medium → Closed** | `events.event_guid` had no database-level uniqueness constraint - Phase 3's own carried-forward hardening item (enforced only at the application layer, `registration.py`'s SELECT-then-INSERT - a TOCTOU gap mitigated in practice by the single-instance lock, not eliminated by it). | Fixed: `db/connection.py`'s `_ensure_event_guid_index()`, a real `UNIQUE INDEX ... COLLATE NOCASE`, same graceful-degradation pattern as the existing `event_id` index. 4 new tests; 2 existing test fixtures and one dev script that happened to share a hardcoded fake GUID across multiple rows were fixed to use distinct values. |
| S-79 | **Low → Closed** | BRD Section 25's "Application Update" operation was reserved but never written - a real schema upgrade left no operational-log trace it had happened. | Fixed: `db/connection.py`'s `_record_schema_upgrade()`, a raw INSERT (not `services/oplog.add()`, to avoid a `db/` → `services/` layering violation), best-effort and never fatal. 2 new tests. |
| S-80 | **Medium → Closed** | `tests/test_no_secrets.py`'s guard covered Blob Storage's key-assignment property name but not the different literal (`accesskey=`) an Azure Communication Services connection string actually uses - a real leaked ACS key would only have been caught by coincidence. | Fixed: a dedicated pattern requiring `accesskey=` + 60 or more base64 characters, safely above every fake test key (40-41 characters) and safely below every real key length observed live (83-88 characters). 1 new test. |
| S-81 | **Medium → Closed** | `install_config.py`'s `bool(data.get("email_enabled", False))` silently mis-parses a quoted-string mistake (`"email_enabled": "false"`) as `True` - Python's `bool("false")` is `True` - which could silently turn email notifications on from a venue operator's typo. | Fixed: `load_config_file()` now requires a real JSON boolean for `email_enabled`, rejected otherwise with a clear message - the same treatment `schedule_password`/`schedule_enabled` already get. 2 new tests. |
| S-82 | **Low → Closed** | The uninstaller never removed a registered Windows Task Scheduler entry - it survives uninstall pointing at a now-deleted executable. | Fixed: `[UninstallRun]` in `ledsync.iss` calls `schtasks /Delete`, tolerant of "not found" exactly like `services/scheduler.py`'s own `unregister()`. Live-tested: uninstall still completes cleanly whether or not a task was ever registered. |
| — | Info, accepted | Two runbook prerequisites Phase 12 promised to carry into Phase 13 (the "Log on as a batch job" right; NTFS access for a scheduled account different from the interactive one) existed only in `workflow.md`, not anywhere a venue IT person setting up the installer would read them. | Added to `installer/README.md`. |
| — | Info, accepted | `exceptions.record_rejection()` (settings/mapping-save rejections) does not expose the table/LED-type/file-name context the lower-level `exceptions.record()` it wraps already supports - a partial reflection of a Phase 3 carried item. | Assessed, not changed: `record()` already carries this context wherever a download/sync-time exception genuinely has it; `record_rejection()` staying narrow (settings-save only, which never has file/table context to give) reads as correct, intentional scope. Recorded rather than silently dropped again. |
| — | Info, accepted (F-87) | No live Azure network call has yet been made from the actual frozen `LEDAssetSync.exe` (only from source, and only the headless exe's non-network paths frozen). `pyinstaller-hooks-contrib`'s `hook-certifi.py` running automatically during the build is a real, positive signal, not proof. | Recommend before real venue deployment: a real Cloud Storage "Test Connection" or Download & Sync from the compiled exe. Not completed here - needs GUI automation for the native WebView2 window, or the real test event's real GUID, neither safely available in this environment. |
| — | Info, resolved as a byproduct | Two apparent "Setup shows a dialog under `/VERYSILENT`" failures during additional scratch-install testing were traced to the test harness (Git Bash's automatic path-conversion mangling `/VERYSILENT` into a bogus path), not a real Inno Setup defect - confirmed by retesting with `MSYS_NO_PATHCONV=1`. `PrivilegesRequiredOverridesAllowed` was still removed from `ledsync.iss` regardless, since an unconditional per-user install is a reasonable simplification on its own merits - this also fully closes the F-83 "opportunistic all-users install" note from the original Phase 13 QA doc. | `ledsync.iss` simplified; re-tested live (per-user install confirmed via `HKEY_CURRENT_USER`, not `HKEY_LOCAL_MACHINE`). |

All nine review findings plus this harness false-alarm were fixed/resolved the same day. `pytest -q`: 1528 passed (was 1519) - a fresh PyInstaller freeze and Inno Setup compile were rebuilt from the fixed source and re-validated end to end.

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | Independent review agent (fresh context, all-phases) | 24/09/26 | **"Ready to ship with fixes." No blockers.** Nine should-fix items (S-78 through S-82 above, plus three info-level notes), all addressed the same day. Full end-to-end secrets/injection/guard-test/cross-phase-drift review confirmed genuinely sound elsewhere - see the QA doc's own summary of the review's findings. |
| User (Vatsan) go-ahead | Vatsan | pending | pending — F-86 (a literal clean-machine run) and F-87 (a live Azure call from the frozen exe) recommended before real venue deployment, both in the QA doc |
