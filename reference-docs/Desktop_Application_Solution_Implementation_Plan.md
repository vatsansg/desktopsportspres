# LED Asset Download and Synchronisation Application — Solution Implementation Plan

**This document is written to be fed directly to the Claude Code implementation session.** It sits on top of, and does not repeat, the following source documents — Claude Code must read all of them before starting:

- `LED_Asset_Download_Desktop_Application_BRD.pdf` (Desktop BRD, v1.2 + Addendum A) — the requirements.
- `LED_Desktop_App_Implementation_Sequence-1.md` (current revision) — the phase/step-by-step build order.

This plan adds the **process** around those phases: how Claude Code should work, what it must produce alongside code at every step, how the UI must be handled, and what Azure-related setup it is responsible for (which, for this application, is materially smaller than for the web application — see Section 4 below).

---

## 1. Kickoff information — CONFIRMED (20 September 2026)

The six items below were the kickoff questions Claude Code needed answered before Phase 0/1 work begins. All six are now confirmed by the project owner. Full detail and rationale for each is also recorded in the Desktop BRD's **Addendum A (Section 39)** — read that section alongside this one.

1. **Storage Account — CONFIRMED shared with the web application.** Account name: `sasportspresentation`. This is the same Storage Account provisioned for the web application; a separate account is not used.
   - **Credential handling:** the actual Storage Account access key is a live secret and is deliberately **not** written into this plan, the BRD, this `ledassetmanagement` reference repository, or any other file that could be committed. It will be supplied directly by the project owner to Claude Code at the start of Phase 4. Claude Code must store it only inside the `desktopsportspres` application repository's own local configuration (e.g. a `.env` file in that project, not this one), and must add that file to `desktopsportspres`'s `.gitignore` as the very first step of Phase 0 — before the first commit — so the key is never pushed to `https://github.com/vatsansg/desktopsportspres`. It must never be written into any file under `C:\vatsan\techprojects\INFRA\ledassetmanagement\`, since that folder is itself a separate tracked Git repository (`github.com/vatsansg/ovrledassetmanagement`).
2. **Test event — CONFIRMED available.** A real event already exists in the live Storage Account: Event ID `1000`, "Star contender Doha", exported via the web application's genuine Export Event function on 18 September 2026 (`exportGuid eb0153b9-b74a-45e8-9f0f-b2c1a1b7b459`), with `_GUID.json`, `_ledassetchangelog.csv`, and populated Table 1 (Inner/Outer/MainLED) and Table 2 (Inner) folders. Use this event for all development and validation from Phase 3 onward.
   - A local mirror of this event's files is also available at `references/0forimplementation/desktop/samplefiles-webassetmgmt/` for offline reference. That local mirror's own folder names (e.g. `Table1/inner`) are a local convenience naming only — build against the authoritative cloud folder naming confirmed in the real change log (`Table 1/Inner`, `Table 1/Outer`, `Table 1/MainLED`), which matches BRD Section 11 exactly.
3. **Venue network for development — CONFIRMED local/simulated for now.** Local/simulated Windows shared folders will be used for Phase 5 (Device and Folder Mapping) and Phase 8 (Push/Synchronise) development; the project owner will supply the specific local folder paths before Phase 5 begins. This does **not** resolve the separate, still-open question of the authentication method for real venue shared folders (BRD Section 36) — that remains to be confirmed before venue deployment.
4. **SMTP/email — CONFIRMED mechanism, plus a new BRD rule.** This application's Phase 11 email notification will use Azure Communication Services — the same service the web application uses for its own Change Log Email Notification. Connection details will be supplied when Phase 11 starts. The project owner also specified: **when the venue machine has no internet connectivity, the application must skip the notification silently rather than fail the operation** — this is now a documented business rule in Desktop BRD Addendum A, Section 39.3, and must be built into Phase 11.
5. **GitHub repository — CONFIRMED.** `https://github.com/vatsansg/desktopsportspres`. Branch strategy and commit message convention should mirror the web application's own process.
6. **Installer/distribution — CONFIRMED.** The installer package is produced to and stored in the `desktopinstaller` container in the same Storage Account (`https://sasportspresentation.blob.core.windows.net/desktopinstaller`). Code-signing is **not** required for the venue-deployed Windows executable.

**What remains genuinely open:** only the items still listed in Desktop BRD Section 36 that are not addressed above — Last Updated Timestamp Cut-off definition/timezone, maximum tables per event, ping-testing vs. shared-folder-access testing, the Windows shared-folder authentication method, whether scheduled runs must work with no user logged in, retry behaviour, checksum validation, local asset retention policy, database schema migration approach, multi-administrator support, and the local change log filename. These should be confirmed at or before the specific phase that needs them, exactly as the Implementation Sequence document already calls out inline — do not treat them as blocking Phase 0 through Phase 4.

---

## 2. Working process for every phase

Apply this to every phase in the Implementation Sequence (Phase 0 through Phase 14), mirroring the process used for the web application so both applications are handed off the same way:

1. **Summarize before building.** Short summary of what the phase requires, and any clarifying questions, before writing code.
2. **Build the phase**, validating against the "Validate before moving on" criteria already specified for each step in the Implementation Sequence document.
3. **Apply the WTT brand skill to all UI work in this phase.** The `wtt-brand` skill is already available at the project level and must be applied to every screen and dialog in this Windows desktop UI, not only to the web application's UI.
4. **Run `wtt-brand` and `ralph-loop` again after the phase is functionally complete**, to fine-tune the UI. As with the web application, `ralph-loop` is expected to already exist at the project level; if it cannot be found, ask the user rather than substituting a different approach.
5. **Create a QA Test Case document for the phase**, following `QA_Test_Case_Template.md` (shared with the web application plan) — adapted where needed for a desktop application (e.g. test cases run on a Windows test machine rather than a browser).
6. **Independent Solution Architect review** of the phase (and cumulatively, everything up to it) before handing off.
7. **Update `workflow.md`** (the desktop application's own copy) — mark the phase complete, note what was actually built, and the date.
8. **Hand off to the user**: phase summary, QA Test Case document, and updated `workflow.md`. Since this application has no shared "live Azure environment" to check in the way the web application does, the hand-off should include a short recorded demo or screenshots of the running application for phases with UI, plus confirmation of what was validated against the real (or agreed test) Storage Account.
9. **Wait for user go-ahead** before starting the next phase.
10. **On go-ahead: commit to GitHub** (`https://github.com/vatsansg/desktopsportspres`) with a commit message matching the phase description, then proceed. Confirm `.gitignore` excludes the local credential file (Section 1.1) before the very first commit in Phase 0.

---

## 3. Security checklist after every release

As with the web application, treat every hand-off in Section 2.8 above as a "release" for the purposes of `Security_Checklist_Template.md` (shared template). For this application, pay particular attention to the checklist items covering: the accepted-risk plain-text local admin credential (Desktop BRD Section 6.1 — already an accepted risk, but must still be reviewed each release to confirm it hasn't expanded in scope), protection of the Storage Account access key in local configuration (Section 1.1 above — confirm it has never been committed to GitHub), and the security of Windows network/shared-folder credentials used for device push (Section 12/20).

---

## 4. Azure-related responsibilities for this application

Unlike the web application, this application does not provision Azure resources and does not use Managed Identity (it is not an Azure-hosted resource — it runs on a venue-local Windows machine). Its only Azure-related responsibility is:

- Connecting to the **existing**, confirmed Storage Account (`sasportspresentation`, Section 1.1 of this plan) using the Storage Account access key, exactly as defined in the Desktop BRD (Sections 4, 14) and its own Application Settings (Section 14 of the BRD).
- Treating that access key as a sensitive configuration value, per the existing RISK note in Desktop BRD Section 4 — it must be protected in `desktopsportspres`'s own local configuration (never committed to source control there, and never written into the `ledassetmanagement` reference repository) even though the application's own login credential is an accepted plain-text risk.
- Producing the installer to the confirmed `desktopinstaller` blob container (Section 1.6 of this plan) at Phase 13.

If, during implementation, it becomes clear this application needs any Azure resource of its own, Claude Code must stop and confirm with the user before provisioning anything — this is out of scope for the current BRD.

---

## 5. Build sequence

Follow the 15 phases already defined in the Implementation Sequence document (Phase 0 through Phase 14), applying the process in Section 2 of this plan to each one. The phases are sequenced so each produces something concretely demonstrable; do not reorder without discussing with the user first, particularly around the Phase 3/4 (GUID validation / Azure Storage connectivity) boundary, since Phase 4 is the first point real Azure credentials are required — those credentials are now confirmed and available (Section 1.1 of this plan), so Phase 4 is unblocked.

---

## 6. Definition of done, per phase

A phase is not complete until all of the following exist and have been handed to the user:

- Working functionality matching the BRD and Implementation Sequence for that phase, validated against the "Validate before moving on" criteria already specified.
- UI built and polished through `wtt-brand` and `ralph-loop`, for every phase that has a UI component.
- A QA Test Case document for the phase.
- A completed Security Checklist for the phase's release.
- `workflow.md` updated to reflect the phase's completion.
- Independent architect review completed.
- User go-ahead received and the corresponding GitHub commit made.

---

## 7. Open items to flag to the user, not decided by this plan

- Several items are still listed as open in Desktop BRD Section 36 ("Still open — to confirm before development begins") and are not resolved by the Section 1 confirmations above — these should still be resolved at or before the specific phase that needs them, not assumed. See Section 1's closing note for the current list.
- Whether venue network access will be available during development is now answered for the near term (Section 1.3 of this plan: local/simulated folders, real venue paths to follow before Phase 5) — but the shared-folder authentication method for the real venue network is still open and affects how realistically Phase 5, 8, and 12 can be fully validated before on-site testing. Flag this explicitly if simulated/local testing is used instead of real venue shared folders.
