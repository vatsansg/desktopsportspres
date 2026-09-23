# Building the installer (Phase 13)

Two steps: freeze the app with PyInstaller, then package it with Inno Setup. Both run from the
project root, on Windows.

## 1. Freeze

```powershell
.\.venv\Scripts\pip install -r requirements-build.txt
.\.venv\Scripts\pyinstaller installer\ledsync.spec --distpath dist --workpath build --noconfirm
```

Produces `dist\LEDAssetSync\` containing two executables sharing one bundle:

- `LEDAssetSync.exe` — the windowed application (no console window).
- `LEDAssetSyncScheduled.exe` — the headless entry point Task Scheduler runs (console-subsystem,
  so a manual test run shows its own output; Task Scheduler's `LogonType=Password` runs it in a
  non-interactive session regardless). A **separate** executable, not one binary dispatching on a
  flag, so it genuinely cannot import pywebview — see `ledsync/services/scheduler.py`'s
  `SCHEDULED_RUN_EXE_NAME` comment.

Smoke-test before packaging:

```powershell
$env:LEDSYNC_DATA_DIR = "$env:TEMP\ledsync-smoke-test"
.\dist\LEDAssetSync\LEDAssetSync.exe --auto-close 5
.\dist\LEDAssetSync\LEDAssetSyncScheduled.exe --data-dir "$env:TEMP\ledsync-smoke-test"
```

Both should exit 0 and write `logs\ledsync.log` under that folder.

## 2. Package

Requires the [Inno Setup Compiler](https://jrsoftware.org/isdl.php) (`ISCC.exe`) — a separate,
free Windows tool, not a Python package.

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\ledsync.iss
```

Produces `installer\output\LEDAssetSync-Setup-<version>.exe`. See `ledsync.iss`'s own header
comments for how fresh-install-vs-upgrade and the pre-install config file are handled.

## The pre-install configuration file

An operator preparing several venue machines can edit **one** `install-config.json`, placed next
to the *compiled* `Setup.exe` (not inside this repo — e.g. on the same USB drive/network share),
so common defaults — Cloud Storage, Email Notification, default Scheduling days/time/account — are
pre-filled after a fresh install instead of being retyped into Settings on every machine.

- Copy `install-config.example.json` to `install-config.json` next to `Setup.exe` and edit it.
- Every field is optional; omit anything you don't want pre-filled.
- Applies **once**, only to a genuinely brand-new installation — never to an upgrade, and never if
  the database already has any saved setting (`ledsync/services/install_config.py`'s
  `should_seed()` — this is checked independently of, and in addition to, the installer's own
  fresh-vs-upgrade detection, so a wrong guess there can never overwrite a configured install).
- **Never include a Scheduling password** — there is no such field, on purpose. Enabling a
  schedule always requires the operator to type the real Windows account password once, directly
  into Settings → Scheduling, after installing (Phase 12's "never stored anywhere" guarantee holds
  here exactly as everywhere else). The config file may pre-fill the account *name* and the
  default day/time only.
- A bad or missing config file is never fatal to the install — it's logged
  (`logs\ledsync.log`) and the operator just configures Settings by hand afterward, exactly as an
  install with no config file at all already works.

See `ledsync/services/install_config.py` for the exact field list and validation (every field goes
through the same `validate_*`/`save_*` functions the Settings pages themselves use).
