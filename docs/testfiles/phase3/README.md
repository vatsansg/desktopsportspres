# Phase 3 test files

Use these with **Add New Event**. None contains a secret. `1000_valid.json` is the real export of the
development test event (Event ID 1000, "Star contender Doha", 18 Sep 2026).

| File | Enter this Event ID | Expected result |
|---|---|---|
| `1000_valid.json` | `1000` | Registered. Dashboard shows 1000, Star contender Doha, **Registered**, Last Updated = the export time (18/09/26 in local time). |
| `1000_valid.json` (again) | `1000` | Blue notice: already registered with this GUID, nothing changed. |
| `1000_reexported_new_guid.json` | `1000` (after 1000 is registered) | **Rejected** (GUID differs) and logged, then redirected to **Re-Register Event**. That screen shows the registered GUID in full but the file's GUID only by its ending (`...f90ae7`), so it cannot just be copied back. Paste `7c9e6679-7425-40de-944b-e07fc1f90ae7` (standing in for "the new GUID copied from the web application") to complete; any other GUID is refused and logged. |
| `2000_second_event.json` | `2000` | Registered (3 tables). |
| `1000_valid.json` | `2000` | Rejected: the file is for event 1000, not 2000. |
| `2001_duplicate_guid_of_1000.json` | `2001` | Rejected: the GUID already belongs to event 1000. |
| `bad_malformed.json` | `3000` | Rejected: not valid JSON. |
| `bad_missing_event_name.json` | `3001` | Rejected: missing `eventName`. |
| `bad_no_tables.json` | `3002` | Rejected: no tables. |
| `bad_invalid_guid.json` | `3003` | Rejected: invalid `exportGuid`. |

Every rejection is recorded in the `exception_log` table (category, operation, event, source, message);
every registration and re-registration is in `operation_log`. Open the scratch database in DB Browser
for SQLite to see them.
