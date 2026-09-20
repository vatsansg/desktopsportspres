# LED Asset Download and Synchronisation Application — Step-Wise Implementation Sequence

Companion document to: **LED Asset Download and Synchronisation Application — Business Requirements Document (v1.2 + Addendum A)**

Purpose of this document: break the desktop application build into small, independently testable steps, so each piece can be built, demonstrated, and validated before the next one starts — rather than validating the whole application at the end. Every step references the BRD section(s) it implements. Steps are grouped into phases; within a phase, steps should be done in order, but phases are sequenced so that each one produces something concretely demonstrable.

Where a step depends on an item still listed as open in the BRD's Section 36, that dependency is called out explicitly — those decisions should be confirmed before that specific step starts, not before the project starts.

> **Revision note (15 September 2026):** validated against Web BRD v2.3 and Desktop BRD v1.1. The web application's six new sections (Champion Winning Moment, Sponsor Ad Duration, OVR Trigger Preview, Change Log Email Notification, Event Log Tab, LED Resolution Configuration) require **no new step or change to this sequence** — all are web-application-side only. The one item with any desktop-side relevance, `champion_winner.mp4`, is confirmed filename-agnostic and already covered by the existing Step 5.1 (see note added there).
>
> **Revision note 2 (15 September 2026):** further validated against Web BRD v2.4 and Desktop BRD v1.2. As of web BRD v2.4, `sponsorsequence.csv` is generated once per table per Inner/Outer LED folder (previously once per event) and therefore now flows through this application's existing download/synchronisation logic (Steps 6–8) exactly like any other asset file — no new step is required, but see the notes added to Steps 6.1 and 6.3, since the same filename can now legitimately appear more than once per event.
>
> **Revision note 3 (20 September 2026):** the Solution Implementation Plan's Section 1 kickoff questions are now all confirmed by the project owner — see Desktop BRD Addendum A (Section 39) for full detail. In summary: the Storage Account is confirmed shared with the web application, with a real test event (`1000 - Star contender Doha`) already present in it; venue network access for development will use local/simulated folders until real venue folders are supplied before Phase 5; the email mechanism is confirmed as Azure Communication Services with a new graceful-skip rule when offline; the GitHub repository and installer distribution location are confirmed. The specific step notes below (4.2, 5.3, 11.1, 13.2) have been updated accordingly. This does **not** resolve the remaining Section 36 items (timestamp cut-off/timezone, max tables, ping-testing, shared-folder authentication method, retry behaviour, checksum validation, retention policy, schema migrations, multi-admin support, local change log filename) — those are still open exactly as before.

---

## How to use this document

For each step: build only what's listed, then check it against "Validate before moving on." Sign off (even informally) before the next step begins. This keeps review small and specific instead of a single large end-of-project review.

---

## Phase 0 — Project Setup (no functional UI yet)

**Step 0.1 — Repository and environment scaffold**
- Set up the Python/Flask project structure, virtual environment, and a bare Windows desktop shell that opens a blank window.
- BRD reference: Section 5 (Technical Architecture).
- Validate before moving on: the blank application launches on a clean Windows machine and closes cleanly. Nothing else.

**Step 0.2 — SQLite database created, empty**
- Create the SQLite file and the seven tables exactly as listed in Section 26 (`events`, `led_mappings`, `download_history`, `sync_history`, `application_settings`, `operation_log`, `exception_log`), with no data in them yet.
- BRD reference: Section 26.
- Validate before moving on: open the `.db` file with any SQLite viewer and confirm all seven tables exist with the listed columns.

---

## Phase 1 — Login (no dashboard content yet)

**Step 1.1 — Fixed admin login**
- Build the login screen and the single fixed administrator account (`admin` / `Admin@123`), stored in local configuration as agreed — in plain text, per the accepted-risk decision.
- BRD reference: Sections 6.1, 6.2.
- Validate before moving on: correct credentials log in and land on an empty (placeholder) dashboard; incorrect credentials show an authentication error. Confirm the credential is changeable by the administrator, as required.

> Reminder for this step only: this plain-text storage decision was explicitly accepted because the application runs only for the duration of an event on a venue-local machine — it is documented here so it isn't mistaken for an oversight later.

---

## Phase 2 — Event Dashboard (read from SQLite only, no Azure yet)

**Step 2.1 — Event List (empty state)**
- Build the Event List / Dashboard screen showing Event ID, Event Name, Last Updated, Status columns, reading directly from the (currently empty) `events` table.
- BRD reference: Section 7.2.
- Validate before moving on: with zero events registered, the dashboard displays cleanly with no errors. Manually insert one test row into `events` and confirm it renders correctly, including the DD/MM/YY HH:MM timestamp format.

---

## Phase 3 — Event Registration and GUID Validation (using a test file, not live Azure yet)

**Step 3.1 — New Event Registration flow (local test file)**
- Build the Add New Event flow. For this step, point it at a manually created local `_GUID.json` test file rather than Azure Storage, so the registration logic can be validated in isolation.
- BRD reference: Section 8.
- Validate before moving on: entering an Event ID and pointing to a valid local test `_GUID.json` successfully creates a new row in `events`.

**Step 3.2 — GUID validation logic**
- Implement the compare-and-reject logic: read the locally recorded GUID, compare it against the GUID in the (test) configuration file, and permit or reject accordingly.
- BRD reference: Sections 9.1, 9.2.
- Validate before moving on: a matching GUID allows registration to proceed; a deliberately mismatched GUID is rejected and the rejection is recorded in the exception log (Section 21.1 — build a minimal version of this log now if not already present).

> Decision needed before Step 3.1 in production: none — this step intentionally uses a local test file to avoid depending on Azure connectivity yet.

---

## Phase 4 — Azure Storage Connectivity (first live connection)

**Step 4.1 — Application Settings: Cloud Storage section**
- Build the Cloud Storage part of the Application Settings screen: storage account, storage container, Storage Account access key, event storage path, asset storage path.
- BRD reference: Section 14 (Cloud Storage Settings).
- Validate before moving on: settings are saved and reloaded correctly after an application restart. Confirm the access key is stored under Application Settings and treated as a sensitive value operationally (Sections 4, 33.3).

**Step 4.2 — Live `_GUID.json` download and registration**
- Repoint Step 3.1's registration flow at real Azure Storage: download the real `_GUID.json` for a real test event, using the access key from Step 4.1.
- BRD reference: Sections 4, 9.1, 10.
- Validate before moving on: registering a real event against a real Azure-hosted event folder succeeds, and the downloaded configuration's fields (eventId, eventName, eventStorageUrl, tables, exportGuid, export metadata) are correctly parsed and stored.

> **Confirmed (20 September 2026, Desktop BRD Addendum A Section 39.1):** the Storage Account is the same one used by the web application (`sasportspresentation`); a real test event (Event ID 1000, "Star contender Doha") already exists in it via a genuine Export Event run on 18 September 2026. The access key itself is supplied via a local, git-ignored credential file (see Solution Implementation Plan Section 1.1) — read it from there rather than asking the user to paste it again.

---

## Phase 5 — LED Structure and Device Mapping (still no downloads yet)

**Step 5.1 — LED structure derived from configuration**
- From the registered event's configuration, derive and display which tables exist and which LED types (Inner/Outer/MainLED) are enabled per table — no independent logic, purely reflecting what `_GUID.json` says.
- BRD reference: Section 11.
- Validate before moving on: for a test event with a known table/LED configuration, the application shows exactly those tables and LED types — nothing extra, nothing missing.
- **[v2.3 note]** Confirms the web application's new `champion_winner.mp4` asset type (web BRD Section 26) requires no change here: this step derives structure from `_GUID.json`'s `tables` field only (Inner/Outer/Main Booleans), never from individual filenames, so the new asset type has no effect on this logic.

**Step 5.2 — Device and folder mapping screen**
- Build the mapping screen: for each enabled LED type per table, allow the administrator to enter an IP address and a Windows shared folder, and persist this to `led_mappings`.
- BRD reference: Section 12.
- Validate before moving on: only enabled LED types are presented for mapping (e.g. a table with only Inner and Outer enabled shows only those two rows). Mappings persist correctly across an application restart.

**Step 5.3 — Device connectivity testing**
- Build the Test Connection function per mapped destination: attempt to access the shared folder (not just ping the device) and report success/failure.
- BRD reference: Section 13.
- Validate before moving on: a reachable, accessible shared folder reports "Connection Successful"; an unreachable or inaccessible one reports "Connection Failed," and both outcomes are recorded in the log.

> **Confirmed for development (20 September 2026):** local/simulated shared folders will be used for this step, already set up for the test event; the project owner will share the actual local folder paths before this phase begins. **Still open:** the authentication method for the Windows shared folders (domain credentials, local venue credentials, or unauthenticated LAN access) remains open (Section 36) for the real venue network and should be confirmed before venue deployment.

---

## Phase 6 — Change Log and Incremental Download Logic (no file downloads yet)

**Step 6.1 — Retrieve and parse `_ledassetchangelog.csv`**
- Implement retrieval of the cloud change log for the registered test event.
- BRD reference: Section 16.
- **[v2.4 note]** Parse the `filename` field as a full relative path (e.g. `Table 1/Inner/sponsorsequence.csv`), not a bare filename — as of web BRD v2.4 the same filename can exist once per table per Inner/Outer folder, so the path is required to identify which specific file changed.
- Validate before moving on: the application correctly downloads and parses the change log for a real test event and lists its entries (without acting on them yet). Include a test event with two tables, each with `sponsorsequence.csv` present in both their Inner and Outer folders, and confirm all four are listed as distinct entries.

**Step 6.2 — Local change log**
- Implement the local change log (recommended filename `_localchangelog.csv`) with the fields listed in Section 17, initially empty.
- BRD reference: Section 17.
- Validate before moving on: the local change log file is created in the correct location with the correct column headers.

**Step 6.3 — Incremental comparison logic**
- Implement the comparison between the cloud change log and the local change log to identify which files are new or updated since the last successful processing.
- BRD reference: Section 18.
- **[v2.4 note]** Match entries by full path (table + LED type + filename), not filename alone, so that (for example) an update to `Table 1/Inner/sponsorsequence.csv` is not confused with, or masks, an update to `Table 1/Outer/sponsorsequence.csv`.
- Validate before moving on: using a test change log with a mix of new, updated, and already-processed entries, confirm the application correctly identifies only the new/updated subset. Include a case where `sponsorsequence.csv` is updated in one table/LED-type folder but not another, and confirm only the updated one is queued for download.

> Decision needed before Step 6.3: the exact definition of the Last Updated Timestamp Cut-off, and whether comparisons use local event time or UTC, is still open (Section 36) and should be confirmed before this step, since it directly determines the comparison logic.

---

## Phase 7 — Asset Download (download only, no push to LED yet)

**Step 7.1 — Download identified files**
- Implement downloading the files identified in Step 6.3 from Azure Storage.
- BRD reference: Section 15 (steps 1–6).
- Validate before moving on: for a test event with a small number of new files, all and only those files are downloaded successfully.

**Step 7.2 — Local asset storage structure**
- Store downloaded files locally, mirroring the Azure Storage folder structure (event / table / LED type).
- BRD reference: Section 19.
- Validate before moving on: the local folder structure exactly matches the event's configured table/LED structure and the Azure Storage layout.

**Step 7.3 — Update local change log after download**
- After each successful download, update the local change log with the download status.
- BRD reference: Section 15 (steps 7–10), Section 17.
- Validate before moving on: re-running the same download operation with no new files results in zero files being re-downloaded (proves incremental logic end-to-end).

---

## Phase 8 — Push / Synchronise to LED Devices

**Step 8.1 — Push downloaded files to mapped destinations**
- Implement pushing each successfully downloaded file to its mapped Windows shared folder (from Step 5.2).
- BRD reference: Section 20.
- Validate before moving on: files pushed to a test destination folder arrive intact and correctly named; synchronisation status is recorded per file.

**Step 8.2 — End-to-end on-demand Download & Sync**
- Wire Steps 6–8 together into the single on-demand Download & Sync action described in Section 15, triggered from the dashboard.
- BRD reference: Section 15 (full flow), Section 24 (dashboard Actions).
- Validate before moving on: triggering Download & Sync for a real test event, from a clean state, results in the correct files being downloaded and correctly pushed to their mapped LED folders, with the dashboard showing progress.

---

## Phase 9 — Error Handling, Exception Log, and Application Log

**Step 9.1 — Exception log**
- Implement the exception log covering the error categories in Section 21 (download, connectivity, GUID, configuration, file access, network/device, synchronisation, permission, missing folder, invalid configuration), with the fields listed in Section 21.1.
- BRD reference: Section 21, 21.1.
- Validate before moving on: deliberately trigger at least three different error categories (e.g. wrong GUID, unreachable shared folder, missing file) and confirm each is correctly categorised and logged.

**Step 9.2 — Application (operational) log**
- Implement the separate operational log covering startup, login, event creation, configuration changes, downloads, syncs, scheduled runs, device tests, errors, and updates, using the DD/MM/YY HH:MM:SS format.
- BRD reference: Section 25.
- Validate before moving on: perform a full login-to-sync cycle and confirm every listed event type appears in the operational log with correct timestamps.

---

## Phase 10 — Remaining Application Settings and Application Dashboard Polish

**Step 10.1 — Complete Application Settings screen**
- Build out the remaining Settings sections not yet covered: Download Settings, Scheduling Settings, Email Settings, and the remaining Application Settings fields.
- BRD reference: Section 14 (all subsections).
- Validate before moving on: every listed setting persists correctly and is reflected in application behaviour where already implemented (e.g. changing the download location actually changes where files are stored).

**Step 10.2 — Full operational dashboard**
- Build out the full dashboard: Event List with Actions column (Add Event, Download & Sync, Test Connections, View Logs, Settings) and the live Operation Status panel.
- BRD reference: Section 24.
- Validate before moving on: all listed actions are reachable and functional from the dashboard; Operation Status updates live during a Download & Sync run.

---

## Phase 11 — Email Notification

**Step 11.1 — Email notification on operation completion**
- Implement the email notification containing the fields in Section 23, sent to the configured IT recipient.
- BRD reference: Section 23, Section 14 (Email Settings).
- Validate before moving on: running a Download & Sync (with notifications enabled) sends a correctly formatted email reflecting the actual operation outcome, for both a fully successful run and a run with induced failures.

> **Confirmed (20 September 2026, Desktop BRD Addendum A Section 39.3):** the email mechanism is Azure Communication Services, the same service used by the web application; connection details will be supplied when this phase starts. **New validation criterion added by this confirmation:** also test the case where the venue machine has no internet connectivity at the time of a run — the operation must still complete and log normally, with the notification silently skipped and recorded as "notification skipped — no connectivity" in the operational log (Section 25), not treated as a failure.

---

## Phase 12 — Scheduled Operation

**Step 12.1 — Scheduling configuration**
- Implement the scheduling fields in Application Settings (enable/disable, scheduled day(s), scheduled time).
- BRD reference: Section 14 (Scheduling Settings).
- Validate before moving on: schedule settings persist correctly.

**Step 12.2 — Windows Task Scheduler integration**
- Implement the scheduled trigger via Windows Task Scheduler invoking the application or a batch process (not a Windows service), running the same Download & Sync flow from Phase 8, followed by the email notification from Phase 11.
- BRD reference: Section 22, Section 31.
- Validate before moving on: a scheduled run fires automatically at the configured time without any user interaction, completes the same Download & Sync flow, updates all logs, and sends the notification email.

> Decision needed before this step: whether the scheduled process must run when no user is logged into Windows is still open (Section 36) — this determines the Task Scheduler configuration ("run whether user is logged on or not") and should be confirmed before this step.

---

## Phase 13 — Installer and Upgrade Handling

**Step 13.1 — New installation path**
- Build the Windows installer for a clean machine: installs the application, creates directories, creates the SQLite database (empty), installs Task Scheduler components, and creates shortcuts.
- BRD reference: Sections 27, 28.1, 29.
- Validate before moving on: running the installer on a genuinely clean Windows machine results in a working, empty application, ready for first event registration — replicate the full first-time workflow in Section 29 end-to-end as the validation for this step.

**Step 13.2 — Upgrade path**
- Build the upgrade path: detect an existing installation, preserve the existing SQLite database, event mappings, and operational history, and update only application code/components.
- BRD reference: Sections 27, 28.2.
- Validate before moving on: install a fresh copy, register a test event and run a sync, then run the installer again as an upgrade — confirm the existing event, mappings, and history are all intact afterward, and the database was not cleared.

> **Confirmed (20 September 2026, Desktop BRD Addendum A Section 39.4):** the installer/update package is produced to and stored in the `desktopinstaller` container in the same Storage Account (`https://sasportspresentation.blob.core.windows.net/desktopinstaller`); code-signing is not required. **Still open:** whether future upgrades will require database schema migrations remains open (Section 36) and should be confirmed before building the upgrade path in earnest.

---

## Phase 14 — Full Workflow and Acceptance Validation

**Step 14.1 — Standard Download and Sync workflow, full run-through**
- Walk through Section 30's full workflow end-to-end on a representative test event, exactly as a venue operator would.
- BRD reference: Section 30.

**Step 14.2 — Scheduled workflow, full run-through**
- Walk through Section 31's full scheduled workflow end-to-end, unattended.
- BRD reference: Section 31.

**Step 14.3 — Functional Requirements and Acceptance Criteria sign-off**
- Go through the Functional Requirements Summary (Section 32, FR-001 to FR-025) and Acceptance Criteria (Section 35, AC-01 to AC-15) as a single checklist and confirm each item individually.
- BRD reference: Sections 32, 35.
- Validate before considering the desktop application complete: every FR and every AC can be demonstrated on request.

---

## Open decisions to track outside this sequence

**Resolved 20 September 2026 (see Desktop BRD Addendum A, Section 39, and Solution Implementation Plan Section 1):** Storage Account and access key, the test event to develop against, venue network approach for development, the SMTP/email mechanism (plus the new offline-skip rule), the GitHub repository, and the installer/update distribution location. These no longer block Step 4.2, 5.3 (development-only), 11.1, or 13.2 — see the updated notes on those steps above.

The following items from the BRD's Section 36 remain genuinely open and are not tied to a single step above, but should still be confirmed at some point before the phase that depends on them, as already noted inline:

- Last Updated Timestamp Cut-off definition and local-time-vs-UTC handling — needed before Step 6.3.
- Windows shared folder authentication method for the real venue network — needed before Step 5.3 can be fully validated on-site (development-only local/simulated testing is already confirmed and unblocked).
- Whether scheduled runs must work with no user logged in — needed before Step 12.2.
- Whether future upgrades will require database schema migrations — needed before Step 13.2.
- Maximum number of supported tables per event.
- Whether IP addresses need ping-testing in addition to shared-folder access testing.
- Retry behaviour for failed downloads and failed synchronisation.
- Whether files should be checksum-validated after download and after push.
- Whether old local assets should ever be deleted, and under what retention rule.
- Whether multiple administrator users will eventually be supported.
- Final confirmation of the local change log filename (`_localchangelog.csv` is the current recommendation).

None of these block starting the build — they only need to be resolved before the specific step named above.
