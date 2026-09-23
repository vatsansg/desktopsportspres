# QA Test Case Document — Desktop Phase 13: Installer and Upgrade Handling

## Header

| Field | Value |
|---|---|
| Application | Desktop |
| Step / Phase | Phase 13 — Installer and Upgrade Handling (Step 13.1 new installation path; Step 13.2 upgrade path) |
| BRD reference(s) | Desktop BRD Section 27 (Installation Requirements), Section 13/14 (Database location), Section 29 (first-time workflow, used as Step 13.1's own validation), Section 36 (schema migration approach, resolved this phase); Desktop BRD Addendum A Section 39.4 (installer distribution, confirmed) |
| Implementation Sequence reference | Steps 13.1, 13.2 |
| Date | 23/09/26 |
| Tested by | Claude Code (automated + a real PyInstaller freeze and a real compiled Inno Setup installer, both built and live-tested on this machine — the Inno Setup Compiler was installed with the owner's explicit go-ahead; see "Live validation" below). |
| Environment | Windows 11 Pro, Python 3.11.9, PyInstaller 6.22.3. |

## Owner decisions applied (23 September 2026) — confirmed before any code was written

- **Packaging: PyInstaller (freeze) + Inno Setup (package).** No code-signing (Addendum A 39.4, already confirmed).
- **Build a general schema-migration framework now**, even though no real structural schema change has ever actually been needed (schema was still version 1) — resolves the Section 36 open item ahead of any concrete need, per the owner's explicit choice (overriding the minimal/deferred option recommended).
- **Pre-install configuration: an editable text file next to the installer**, not an interactive installer wizard — applies once, to a brand-new install only, never an upgrade.
- **The Scheduling Windows account password is never accepted in that file** — Phase 12's "never stored anywhere" guarantee holds here exactly as everywhere else. The file may pre-fill the account *name* and default day/time only; the password is still typed once, directly into Settings → Scheduling, after installing.

## What was built

- **`ledsync/db/migrations.py`** (NEW): a general, versioned migration runner. `SCHEMA_VERSION` bumped 1 → 2 by converting the one real ad-hoc schema change this application has ever made (Phase 10's `events.cutoff_enabled`/`cutoff_time` columns, previously an unconditional idempotent helper run on every startup) into the first real, versioned `Migration`. A brand-new database is still created directly at the current `SCHEMA_VERSION` from `schema.DDL` and never runs a migration at all; migrations only ever bring an *existing* database up to date, one version at a time, each inside its own transaction, via `db/connection.py`'s `init_db`.
- **`ledsync/services/install_config.py`** (NEW): validates and applies a pre-install JSON configuration file to a brand-new database only (`should_seed()`), through the exact same `validate_*`/`save_*` functions the Settings pages already use — nothing here is reimplemented, so a malformed value is refused exactly as if typed into the UI. Explicitly refuses a file containing `schedule_password` or `schedule_enabled` (turning scheduling on needs a real Task Scheduler registration, which needs a password this file must never contain) with a clear, specific message for each. `settings.py` gained one small owned helper, `has_any_setting()`, so this module never touches the settings table directly (keeps the existing SCOPE GUARD intact — see Security doc).
- **`ledsync/main.py`**: new `--seed-config PATH` flag. Runs the seeding step (inside the same single-instance lock as everything else), then exits — no window, no WebView2 check at all (a build/CI environment with no WebView2 runtime can still run this). A bad or missing config file is logged and never fatal to the install.
- **`ledsync/services/scheduler.py`**: `_command_and_args()` — a frozen build has no `python.exe`/`-m` available on the venue machine at all, so the registered Task now points at a dedicated `LEDAssetSyncScheduled.exe` (installed alongside the main application) instead; running from source is unchanged (`-m ledsync.scheduled_run`, exactly as Phase 12 built it).
- **`installer/ledsync.spec`** (NEW): a PyInstaller spec producing **two** executables from one shared bundle — `LEDAssetSync.exe` (windowed) and `LEDAssetSyncScheduled.exe` (console, headless) — rather than one binary dispatching on an argv flag, so the shipped `LEDAssetSyncScheduled.exe` genuinely cannot import pywebview, keeping Phase 12's "no GUI toolkit" guarantee true of the real shipped artifact.
- **`installer/ledsync.iss`** (NEW): an Inno Setup script. Installs to a per-user folder (`PrivilegesRequired=lowest`, no admin needed); never touches the *data* folder (`%LocalAppData%\LEDAssetSync`) — only the *install* folder — which is what makes "preserve the existing database on upgrade" (BRD 13.2) hold automatically, with no special-case upgrade code needed at all. Runs `--seed-config` post-install only when a config file is found next to `Setup.exe` **and** the data folder looks fresh (defense in depth: `install_config.should_seed()` is checked again, independently, inside the application itself).
- **`installer/install-config.example.json`** (NEW), **`installer/README.md`** (NEW): the documented field schema and the full build/package procedure.
- **`requirements-build.txt`** (NEW): PyInstaller as a build-only dependency, never shipped to a venue machine.
- 27 new tests (`tests/test_phase13_installer.py`): the migration runner's generic mechanics plus the one real migration against a simulated pre-Phase-10 database; `install_config.py`'s file loading/validation/seeding, including the schedule-password/schedule-enabled refusals and the underscore-prefixed-documentation-key exemption; the frozen-vs-source Task command/arguments; `main.py`'s `--seed-config` integration, including that it never checks for WebView2.

## Live validation — DONE, on this machine, 23 September 2026

**PyInstaller freeze:**
1. `pyinstaller installer\ledsync.spec` — built cleanly on the first attempt (no missing hidden-import iteration needed; `pyinstaller-hooks-contrib`'s existing `hook-webview.py` covered pywebview's dynamic backend loading).
2. `LEDAssetSyncScheduled.exe --data-dir <scratch folder>` — exited 0, `logs\ledsync.log` and `ledsync.db` created correctly, `Scheduled Run: Started → Success ("No events are registered")` oplog rows correct.
3. `LEDAssetSync.exe --seed-config <install-config.json>` (`LEDSYNC_DATA_DIR` pointed at a scratch folder) — exited 0, no window opened, applied `cloud storage, email notification, scheduling defaults (not enabled), log retention` and logged exactly that; the database was inspected directly afterward and every value matched the input file; `email_enabled`/`schedule_enabled` both correctly `0` (never turned on by the file, per the owner's decision above).
4. `LEDAssetSync.exe --auto-close 5` (no config file, fresh scratch data folder) — a **real WebView2 window opened**, the UI server started, `Application Startup: Success` was logged, and the window closed cleanly after 5 seconds with no errors. Confirms Flask's template/static resolution works correctly inside the frozen bundle (the highest-risk part of freezing a Flask+pywebview app) — not just that the process starts.

**Inno Setup packaging** (Inno Setup Compiler installed with the owner's explicit go-ahead — official installer, `https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe`, ~10.1 MB, installed silently):

5. `ISCC.exe installer\ledsync.iss` — compiled cleanly (after one fix — see F-85/S-77 below) to a real `LEDAssetSync-Setup-0.1.0.exe` (~24 MB).
6. **First real install run** (silent, redirected to a scratch install folder for safety — this machine's real default data folder already held the owner's own testing history and was deliberately never touched by any of this validation): files copied correctly, Start Menu and Desktop shortcuts created, an uninstall registry entry written. Running the generated `unins000.exe` afterward removed every file, shortcut, and registry entry cleanly — confirmed nothing left behind.
7. **BRD Step 13.2's own validation criterion, directly proven**: installed fresh (scratch install + scratch data folder) → inserted a real test event + LED mapping directly into that database (simulating event registration) → ran the same `Setup.exe` again over the same install folder (an upgrade) → confirmed the event and mapping were **still present, byte-for-byte**, afterward. This holds structurally, not by luck: `[Files]` in `ledsync.iss` only ever targets the *install* folder (`{app}`), never the *data* folder (`%LocalAppData%\LEDAssetSync`) — so an upgrade cannot touch the database no matter what Inno Setup's own internal "is this an upgrade" bookkeeping decides.
8. **A real bug found and fixed live**: the "Launch now" postinstall step fired even under `/VERYSILENT` (Inno Setup's `skipifsilent` flag did not reliably suppress it in this environment) — the very first scratch-install test unexpectedly launched a real `LEDAssetSync.exe` window against this machine's **real default data folder** (harmless in itself — the same idempotent startup sequence a normal launch performs — but not something a silent/unattended install should ever risk). Caught immediately, the process was closed gracefully, and the real data folder was confirmed unaffected (only the expected, harmless `Application Startup` log line). **Fixed** by removing the postinstall launch entry entirely — recompiled, and the retest confirmed no launch occurs. See F-85/S-77.

**Not done** (out of scope for this environment): a literal "genuinely clean Windows machine" run (BRD Step 13.1's own wording) — this machine is the development machine, not a clean one. Everything above was validated as rigorously as possible without one, using scratch install/data locations throughout. Recorded as F-86.

## Test cases — automated

| ID | Description | Status |
|---|---|---|
| TC-A01 | **Migration runner (generic):** applies every migration strictly between current and target version, in order, skipping anything beyond the target; a no-op when already at the target version | Pass |
| TC-A02 | **Migration runner (real):** a simulated pre-Phase-10 database (`user_version=1`, no `cutoff_enabled`/`cutoff_time` columns) reaches the current `SCHEMA_VERSION` and gains both columns, in the right column order, via `init_db` alone — the existing row survives | Pass |
| TC-A03 | **`install_config.load_config_file`:** valid JSON parses; invalid JSON, a non-object document, and a missing file are all refused with a clear message; `schedule_password` and `schedule_enabled` are refused outright with a specific explanation for each; an unrecognised field is refused; underscore-prefixed documentation keys (e.g. `_comment`) are silently ignored | Pass |
| TC-A04 | **`install_config.should_seed`/`seed`:** true only for a database with no saved settings at all; applies cloud storage, email notification, scheduling *defaults* (never enabled), download retry, log retention and folders through the real `settings.py` functions; refuses to run against an already-configured database, leaving it untouched; propagates the exact same validation Settings pages already enforce (a bad value is refused, nothing partially saved); an empty config object is a safe no-op | Pass |
| TC-A05 | **Frozen vs. source Task command:** `scheduler._command_and_args()` builds `-m ledsync.scheduled_run --data-dir ...` for a source run, and the dedicated `LEDAssetSyncScheduled.exe` (found next to the running frozen executable) for a frozen one; the generated Task XML reflects whichever is active | Pass |
| TC-A06 | **`main.py --seed-config` integration:** applies a real file to a brand-new database; is silently a no-op (not an error) against an already-configured one; never raises on a malformed file (logged instead); never calls `platform_checks.webview2_version()` at all in this mode | Pass |
| TC-A07 | **The example install-config file stays in sync with the real field schema** — `installer/install-config.example.json` is loaded through the real validator in the test suite, so a future field rename/removal fails the build instead of silently going stale | Pass |

`.\.venv\Scripts\python -m pytest -q` → **1519 passed, 14 skipped** (unrelated live-Azure-Storage tests from earlier phases).

## Open defects / follow-ups

| ID | Description | Severity | Status |
|---|---|---|---|
| F-83 | The installer's `PrivilegesRequired=lowest` design means `--seed-config` runs as whichever account launches `Setup.exe`, with no elevation — the seeded database lands in *that* account's own `%LocalAppData%`. Almost always correct (the installing operator is also the daily user), but worth the owner confirming this matches how venue machines are actually set up, especially if installation is ever done by IT staff on behalf of a different operator account. Observed live: on an account with local admin rights, Setup opportunistically wrote shortcuts/registry to ALL-USERS locations (`C:\ProgramData`, `C:\Users\Public\Desktop`, `HKEY_LOCAL_MACHINE`) rather than strictly per-user ones, even with `/CURRENTUSER` passed — Inno Setup's own documented behaviour when admin rights are available and `PrivilegesRequiredOverridesAllowed` permits it. Not a defect (the generated uninstaller reverses either choice cleanly, confirmed live) but worth the owner being aware of. | Info | Documented in `installer/ledsync.iss`'s own header; accepted, tell me if wrong |
| F-84 | No installer icon (`.ico`) is set for either executable — `WTT-Logo.png` is not an `.ico` file. Cosmetic only; both executables currently show the default PyInstaller/Windows icon. | Low | Accepted; owner to supply a real `.ico` when convenient |
| F-85 / S-77 | **Closed, found and fixed live.** The "Launch {#MyAppName} now" postinstall `[Run]` entry fired even under `/VERYSILENT` (`skipifsilent` did not reliably suppress it in this environment) — a scratch-install test run unexpectedly launched a real application window against this machine's real default data folder. | Fixed by removing the postinstall launch entry entirely from `ledsync.iss` — recompiled and retested; confirmed no launch occurs on a silent install. |
| F-86 | No literal "genuinely clean Windows machine" run has been done (BRD Step 13.1's own wording) — this is the development machine. Everything else under "Live validation" was tested as rigorously as possible without one (scratch install/data locations, direct file/registry inspection, a real upgrade-preserves-data proof). | Info | **Needs the owner** — ideally a real clean machine or VM, following `installer/README.md` |

## Independent Solution Architect review

Not yet run for this phase in isolation — per the owner's explicit instruction (23 Sep 2026), a single, broader end-to-end architect review covering **every** phase (0–13) is scheduled ahead of final hand-off, rather than a Phase-13-only pass followed by a separate all-phases pass. See that review's own report for findings and outcome once complete.

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Independent Solution Architect review | — | — | Pending — covered by the all-phases review requested ahead of hand-off (see above) |
| User (Vatsan) go-ahead | Vatsan | pending | pending — needs the Inno Setup packaging + clean-machine/upgrade validation (F-82) and the all-phases review |
