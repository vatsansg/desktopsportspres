# LED Asset Download and Synchronisation Application

Windows desktop app (Python / Flask / SQLite, native window via pywebview + Edge WebView2) that
downloads sports-presentation assets from the web application's Azure Storage Account and pushes
them to LED display shared folders at an event venue. Read-only against Azure.

Requirements live in `reference-docs/` (BRD v1.2 + Addendum A, Implementation Sequence, Solution
Implementation Plan). Build progress is tracked in `reference-docs/workflow.md`.

## Develop

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q          # run tests
.\.venv\Scripts\python -m ledsync            # launch the app
.\.venv\Scripts\python -m ledsync --auto-close 5   # launch, then close after 5s (smoke test)
```

Requires the Microsoft Edge **WebView2 runtime** (preinstalled on Windows 11).

## Layout

| Path | Purpose |
|---|---|
| `ledsync/main.py` | Entry point: init DB, start loopback Flask server, open window |
| `ledsync/web/` | Flask app, templates, static assets (UI layer) |
| `ledsync/services/` | Business logic (storage, sync, change log, scheduling, email) - from Phase 3 |
| `ledsync/db/` | SQLite schema and connection (BRD Section 26) |
| `ledsync/config.py` | All filesystem locations |
| `tests/` | Automated tests |
| `docs/` | Per-phase QA Test Case and Security Checklist documents |

## Local data

Default data directory: `%LOCALAPPDATA%\LEDAssetSync` (override with `LEDSYNC_DATA_DIR`). It holds
`ledsync.db` and `logs\ledsync.log` (diagnostic log — startup failures land here and in a dialog).

## Secrets

The Storage Account access key is required from Phase 4. It goes **only** in a local, git-ignored
`.env` (copy `.env.example`). Never commit it, and never write it into any document.
`tests/test_no_secrets.py` fails if anything key-shaped becomes committable.

## Branching / commits

Mirrors the web application: one `phase-N-<name>` branch per phase, merged to `main`; commit
messages read `Phase N: <description>`.
