# QA Test Case Document — Desktop Phase 14: Full Workflow and Acceptance Validation

## Header

| Field | Value |
|---|---|
| Application | Desktop |
| Step / Phase | Phase 14 — Full Workflow and Acceptance Validation (Step 14.1 standard workflow run-through; Step 14.2 scheduled workflow run-through; Step 14.3 Functional Requirements / Acceptance Criteria sign-off) |
| BRD reference(s) | Section 29 (First-Time Installation Workflow), Section 30 (Standard Download and Sync Workflow), Section 31 (Scheduled Workflow), Section 32 (Functional Requirements Summary, FR-001–FR-025), Section 33 (Non-Functional Requirements), Section 34 (Business Rules), Section 35 (Acceptance Criteria, AC-01–AC-15) |
| Implementation Sequence reference | Steps 14.1, 14.2, 14.3 |
| Date | 24/09/26 |
| Tested by | Claude Code (traceability against the actual current codebase and test suite, plus every live validation performed across Phases 0–13 — this phase adds no new code; it is a verification pass over what already exists) |
| Environment | Windows 11 Pro, Python 3.11.9; `pytest -q` → 1528 passed, 14 skipped, as of Phase 13's completion |

This phase is unlike every phase before it: **no new application code was written**. BRD Section 36
("Open Points") notes several items "still open — to confirm before development begins"; by this
point every one of them was resolved by an explicit owner decision during the phase it first
mattered (see the per-item notes below and each phase's own "Deviations" section in
`reference-docs/workflow.md`). Step 14.3's job is to go through Sections 32 and 35 one line at a
time and confirm — with a real citation, not just an assertion — that each is actually built and
tested, not merely believed to be.

## Step 14.1 — Standard Download and Sync Workflow (BRD Section 30), full run-through

> User selects Event → Validate Configuration → Validate GUID → Connect to Azure Storage → Retrieve
> `_ledassetchangelog.csv` → Compare with Local Change Log → Identify New/Updated Files → Download
> Files → Validate Downloads → Update Local Change Log → Push to LED Shared Folders → Verify
> Synchronisation → Update SQLite → Update Logs → Display Result

Every step of this sequence is exercised, end to end, by `services/downloads.run_job` (Phase 7/8) —
the exact same function the Dashboard's **Download & Sync** button calls, and the exact same
function a scheduled run calls (Step 14.2). Confirmed with no drift between the two call sites
during the Phase 13 pre-hand-off review (see that review's "cross-phase observations").

Live, real-world run-throughs of this exact workflow, not just automated fakes, happened repeatedly
across this project:
- Phase 7/8: real Azure Storage account, real test event `1000 — Star contender Doha`, real files
  downloaded and pushed to simulated LED destinations.
- Phase 13, twice: once during the owner's own compiled-installer validation (real event, real
  Download & Sync, from the actual frozen `LEDAssetSync.exe`); once earlier, headlessly, via
  `LEDAssetSyncScheduled.exe` against the same real event (Identified 2, downloaded 1, synchronised
  1, errors 0).

Automated coverage of every individual step: `tests/test_phase6_changelog.py`,
`test_phase6_compare.py` (change-log comparison, identify new/updated files);
`tests/test_phase7_engine.py`, `test_phase7_job.py` (download, validate, update local change log);
`tests/test_phase8_sync.py` (push to LED destinations, verify synchronisation, update SQLite);
`tests/test_phase9_logs.py` (update logs, display result). **Pass.**

## Step 14.2 — Scheduled Workflow (BRD Section 31), full run-through, unattended

> Windows Task Scheduler → Trigger Application/Batch → Load Configuration → Validate Event/GUID →
> Retrieve Change Log → Identify Changes → Download Assets → Synchronise Assets → Update
> Database/Logs → Generate Result → Send IT Email

Built in Phase 12 (`ledsync/scheduled_run.py`, `services/scheduler.py`), packaged in Phase 13
(`LEDAssetSyncScheduled.exe`, a dedicated headless executable that cannot import any GUI toolkit).
**Live-validated for real, twice, on a real Entra ID-joined Windows machine** (Phase 12): a real
Windows Task Scheduler entry registered and triggered a real, unattended run — headless, under a
Windows account that was never itself interactively signed in — that identified, downloaded, and
synchronised real files and sent a real completion email. Not merely simulated: this is where three
real environmental/application bugs were found and fixed live (the "Log on as a batch job"
prerequisite, a cross-account data-folder access gap, and the root cause of Phase 11's F-76/S-69).

The one qualification carried forward honestly rather than silently dropped: the real trigger used
was a dedicated, never-interactively-signed-in account executing the task — not a literal "sign out
of Windows entirely" pass repeated after the later email fixes (Phase 12 QA doc's F-81, accepted by
the owner as a lower-priority follow-up, not a blocker). Automated coverage:
`tests/test_phase12_scheduled.py` (23 cases — lock, XML/registration, orchestration, one-bad-event
resilience, lock contention). **Pass.**

## Step 14.3 — Functional Requirements Summary (BRD Section 32, FR-001–FR-025)

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| FR-001 | Windows desktop application | **Met** | The whole application; Phase 0 (pywebview + Edge WebView2 native window) |
| FR-002 | Python backend | **Met** | Python 3.11 throughout |
| FR-003 | Flask backend framework | **Met** | `ledsync/web/app.py`, Phase 0 |
| FR-004 | SQLite database | **Met** | `ledsync/db/schema.py`, Phase 0 |
| FR-005 | Local user authentication (fixed admin account) | **Met** | Phase 1; `services/auth.py`; accepted-risk plain-text credential per BRD §6.1 |
| FR-006 | Event management and dashboard | **Met** | Phase 2; `dashboard.html`, `views.py` |
| FR-007 | Event GUID validation against `_GUID.json` | **Met** | Phase 3; `services/registration.py`; `test_guid_mismatch_is_rejected_logged_and_redirects_to_reregistration` (AC-04 below) |
| FR-008 | JSON event configuration, downloaded from Azure Storage | **Met** | Phase 3/4; `services/cloud.py`, `services/storage.py` |
| FR-009 | Multiple tables supported | **Met** | Phase 5; `services/structure.py`, `services/mappings.py` |
| FR-010 | Inner/Outer/Main LED types supported | **Met** | Phase 5; `structure.py`'s LED-type canonicalisation |
| FR-011 | Device/folder mapping (local, manual) | **Met** | Phase 5; `event_details.html`'s Device Mapping section |
| FR-012 | Connection testing per destination folder | **Met** | Phase 5; per-row and **Test All Connections**; `services/mappings.validate_shared_folder`/device test |
| FR-013 | On-demand download and sync | **Met** | Phase 7/8; Dashboard's **Download & Sync** button, `downloads.run_job` |
| FR-014 | Asset synchronisation/push to LED devices | **Met** | Phase 8; `services/sync.py` |
| FR-015 | Incremental download logic | **Met** | Phase 6/7; `services/changes.py` (change-log comparison), `tests/test_phase6_compare.py`, `test_phase7_review.py` |
| FR-016 | Local change log | **Met** | Phase 6; `_localchangelog.csv`, `services/changes.py` |
| FR-017 | Exception log | **Met** | Phase 9; `services/exceptions.py`, the Exceptions tab of Logs |
| FR-018 | Scheduled operation via Windows Task Scheduler | **Met** | Phase 12; see Step 14.2 above |
| FR-019 | Email notification (Should) | **Met** | Phase 11; `services/email_notify.py`, Azure Communication Services (Addendum A §39.3 supersedes the base BRD's SMTP wording — owner-confirmed) |
| FR-020 | Application settings | **Met** | Phase 10; six Settings sections |
| FR-021 | Windows installer | **Met** | Phase 13; `installer/ledsync.iss`, real compiled `Setup.exe`, live-tested |
| FR-022 | New installation handling | **Met** | Phase 13; a fresh install creates an empty database, ready for first event registration — live-tested |
| FR-023 | Application upgrade handling | **Met** | Phase 13; re-running the installer over an existing install |
| FR-024 | Preserve database during upgrades | **Met** | Phase 13; **directly proven live** — a real test event + LED mapping inserted, the installer re-run over the same install folder, confirmed byte-for-byte intact afterward (Business Rule 9) |
| FR-025 | Operational logging | **Met** | Phase 9 (and Phase 13's own fix: a real schema upgrade now also logs an `Application Update` row, closing a gap the all-phases review found) |

**All 25 met.**

## Acceptance Criteria (BRD Section 35, AC-01–AC-15)

| ID | Criterion | Status | Evidence |
|---|---|---|---|
| AC-01 | Login | **Met** | Phase 1; `tests/test_auth.py` |
| AC-02 | Event Display (ID, Name, Last Updated) | **Met** | Phase 2; Dashboard events table |
| AC-03 | New Event registration | **Met** | Phase 3; `event_new.html`, `test_cloud_registration.py` |
| AC-04 | GUID Validation rejects a mismatch | **Met** | Phase 3; `tests/test_event_routes.py::test_guid_mismatch_is_rejected_logged_and_redirects_to_reregistration` |
| AC-05 | Reads `_GUID.json`, identifies tables/LED types | **Met** | Phase 3/5; `services/structure.py` |
| AC-06 | Map an LED type to an IP/shared folder | **Met** | Phase 5; Device Mapping form |
| AC-07 | Connection test succeeds/fails correctly | **Met** | Phase 5; per-device Test, `services/mappings.py`, `test_phase5_services.py` |
| AC-08 | Downloads new/updated assets | **Met** | Phase 7; `services/transfer.py`, `test_phase7_engine.py` |
| AC-09 | Incremental download (unchanged files not re-fetched) | **Met** | Phase 6/7; `test_phase6_compare.py`, `test_phase7_review.py` ("already processed" cases) |
| AC-10 | Synchronisation to configured LED destinations | **Met** | Phase 8; `services/sync.py`, `test_phase8_sync.py` |
| AC-11 | Errors recorded in the exception log | **Met** | Phase 9; `services/exceptions.py`, `test_phase9_logs.py` |
| AC-12 | Scheduled operation executes via Task Scheduler | **Met** | Phase 12; see Step 14.2 above — live-validated for real |
| AC-13 | IT recipient receives an operation status email | **Met** | Phase 11/12; live-validated (real email received and confirmed by the owner) |
| AC-14 | New installation creates a clean SQLite database | **Met** | Phase 0/13; `db/connection.py::init_db`, live-tested via the real installer |
| AC-15 | Upgrade preserves existing event configuration/database | **Met** | Phase 13; same live proof as FR-024 above |

**All 15 met.**

## Non-Functional Requirements (BRD Section 33) and Business Rules (Section 34) — confirmed, not re-litigated

Already covered by the accumulated per-phase Security Checklists and the Phase 13 all-phases
architect review (verdict: "Ready to ship with fixes, no blockers"); not repeated here in full.
Spot-confirmed for this sign-off: §33.1 Reliability (one bad file/device never stops the others,
Phase 7/8's own principle, reused unchanged in Phase 12's scheduled path); §33.3 Security (the
Storage Account key and every later secret type are never stored as plain text where avoidable
except the one BRD-accepted exception, §6.1's admin credential); §33.5 Recoverability (re-running a
failed operation never corrupts existing local state — the whole incremental-download design);
§33.6 Auditability (`operation_log`/`exception_log`, every significant action). Business Rule 9
("upgrades must preserve the existing SQLite database") is the one this phase proves most directly
— see FR-024/AC-15 above. Business Rule 13 ("never write, rename, or delete files within the web
application's Azure Storage structure") is enforced structurally, not just by convention — the
AST-based guard (`tests/test_storage.py`) blocklists every mutating Azure SDK call by parsed method
name, confirmed by the all-phases review to have no realistic evasion.

## BRD Section 36 ("Open Points") — resolution status

Every item marked "still open" in the original BRD was resolved by an explicit owner decision
during the phase it first became load-bearing, not left open into this final phase:

| Open point | Resolved | Phase |
|---|---|---|
| Timestamp Cut-off definition, local time vs. UTC | Daily, UTC, per-event | 10 |
| Maximum tables per event | No hard limit enforced | 5 |
| IP ping-testing vs. folder accessibility | Folder/device accessibility only, not ICMP ping | 5 |
| Shared-folder authentication method | The Windows account the application runs under | Carried, accepted (Phase 5 onward) |
| SMTP/email service | Azure Communication Services (Addendum A §39.3 supersedes) | 11 |
| Run with nobody logged into Windows | Yes, required — resolved | 12 |
| Retry behaviour | Configurable count/delay, Settings → Download | 10 |
| Checksum validation after download/push | Size + timestamp fingerprint, not a cryptographic checksum | 7 |
| Old local asset retention/deletion | Not deleted; log retention (days) is configurable, assets are not | 10 |
| Application update distribution location | `desktopinstaller` Azure Storage container (Addendum A §39.4) | 13 |
| Database schema migrations for future upgrades | A general, versioned migration runner now exists | 13 |
| Multiple administrator users | Not supported — single fixed credential remains the accepted risk (§6.1) | Carried, accepted |
| Local change log filename | `_localchangelog.csv`, as the BRD's own recommendation | 6 |

## Sign-off

| Role | Name | Date | Outcome |
|---|---|---|---|
| Traceability review (this document) | Claude Code | 24/09/26 | All 25 Functional Requirements and all 15 Acceptance Criteria confirmed met, each with a specific evidence citation. No new code required. |
| User (Vatsan) go-ahead | Vatsan | 24/09/26 | **Go-ahead given.** Merged to `main` and pushed. |
