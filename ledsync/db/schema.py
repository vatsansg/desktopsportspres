"""SQLite schema - BRD Section 26, seven tables.

Columns are exactly those listed in Section 26, plus the extensions below that
the project owner approved on 20 Sep 2026 ("Extend now"). Extensions are marked
`-- EXT` so they are easy to audit against the BRD.

  events.configuration_json     Section 7.1 requires storing the downloaded
                                _GUID.json content; Section 26's
                                `configuration_file` holds the source location.
  exception_log (5 columns)     Section 21.1 requires table, LED type, file name,
                                source and destination on every exception
                                record; Section 26 lists only 7 columns.
  events.cutoff_enabled,        Phase 10, owner decision (22 Sep 2026, refined same day): the BRD
  events.cutoff_time            14/18 "Last Updated Timestamp Cut-off" is a recurring DAILY time of
                                day (UTC, e.g. "21:00"), not a one-time date, and is per event, not a
                                single application-wide setting - so both live on the event row
                                rather than in `application_settings`. `cutoff_enabled` is the
                                emergency on/off switch; `cutoff_time` is kept even while disabled so
                                it need not be retyped.

Timestamps are stored as TEXT. The storage format (and local-time-vs-UTC) is
deliberately not decided here - it is an open BRD Section 36 item that must be
settled before Step 6.3. Display formatting (DD/MM/YY HH:MM) is a UI concern.

The logs (operation_log, exception_log) have no foreign key to `events`: a
rejected registration (Step 3.2) must be loggable for an event that never made
it into `events`.

Schema version is tracked with `PRAGMA user_version` (not a table). Bringing an existing database
from an older SCHEMA_VERSION up to the current one is `db/migrations.py`'s job (Phase 13, resolves
the Section 36 "schema migration approach" item) - this module only ever describes the CURRENT
schema; a brand-new database is created directly from it and never runs a migration.
"""

SCHEMA_VERSION = 2

DDL = """
CREATE TABLE IF NOT EXISTS events (
    event_id              TEXT PRIMARY KEY,
    event_name            TEXT NOT NULL,
    event_guid            TEXT,
    configuration_file    TEXT,
    configuration_json    TEXT,   -- EXT
    configuration_version TEXT,
    last_updated          TEXT,
    last_download         TEXT,
    last_sync             TEXT,
    status                TEXT,
    cutoff_enabled        INTEGER NOT NULL DEFAULT 0,  -- EXT (Phase 10, BRD 14/18: the daily cut-off's on/off switch)
    cutoff_time           TEXT   -- EXT (Phase 10, BRD 14/18: the daily cut-off's time of day, UTC "HH:MM")
);

CREATE TABLE IF NOT EXISTS led_mappings (
    mapping_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id               TEXT NOT NULL REFERENCES events(event_id),
    table_number           INTEGER NOT NULL,
    led_type               TEXT NOT NULL,
    ip_address             TEXT,
    shared_folder          TEXT,
    enabled                INTEGER NOT NULL DEFAULT 1,
    last_connection_test   TEXT,
    connection_status      TEXT,
    UNIQUE (event_id, table_number, led_type)
);

CREATE TABLE IF NOT EXISTS download_history (
    download_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id           TEXT NOT NULL REFERENCES events(event_id),
    file_name          TEXT NOT NULL,
    table_number       INTEGER,
    led_type           TEXT,
    source_path        TEXT,
    local_path         TEXT,
    source_timestamp   TEXT,
    download_timestamp TEXT,
    status             TEXT
);

CREATE TABLE IF NOT EXISTS sync_history (
    sync_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id       TEXT NOT NULL REFERENCES events(event_id),
    file_name      TEXT NOT NULL,
    destination    TEXT,
    sync_timestamp TEXT,
    status         TEXT,
    error_message  TEXT
);

CREATE TABLE IF NOT EXISTS application_settings (
    setting_name  TEXT PRIMARY KEY,
    setting_value TEXT
);

CREATE TABLE IF NOT EXISTS operation_log (
    log_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id  TEXT,
    operation TEXT,
    timestamp TEXT NOT NULL,
    status    TEXT,
    message   TEXT
);

CREATE TABLE IF NOT EXISTS exception_log (
    exception_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id          TEXT,
    timestamp         TEXT NOT NULL,
    operation         TEXT,
    category          TEXT,
    message           TEXT,
    resolution_status TEXT,
    table_number      INTEGER,  -- EXT (Section 21.1)
    led_type          TEXT,     -- EXT (Section 21.1)
    file_name         TEXT,     -- EXT (Section 21.1)
    source            TEXT,     -- EXT (Section 21.1)
    destination       TEXT      -- EXT (Section 21.1)
);

-- The Logs page (Phase 9) filters and sorts both logs by these columns; without an index a large
-- installation would scan the whole table on every page view.
CREATE INDEX IF NOT EXISTS ix_operation_log_timestamp ON operation_log (timestamp);
CREATE INDEX IF NOT EXISTS ix_operation_log_event_id ON operation_log (event_id);
CREATE INDEX IF NOT EXISTS ix_operation_log_operation ON operation_log (operation);
CREATE INDEX IF NOT EXISTS ix_exception_log_timestamp ON exception_log (timestamp);
CREATE INDEX IF NOT EXISTS ix_exception_log_event_id ON exception_log (event_id);
CREATE INDEX IF NOT EXISTS ix_exception_log_resolution_status ON exception_log (resolution_status);
"""

# Expected columns per table, in order. Used by tests and by the startup
# integrity check so schema drift is caught rather than silently tolerated.
TABLES: dict[str, list[str]] = {
    "events": [
        "event_id", "event_name", "event_guid", "configuration_file",
        "configuration_json", "configuration_version", "last_updated",
        "last_download", "last_sync", "status", "cutoff_enabled", "cutoff_time",
    ],
    "led_mappings": [
        "mapping_id", "event_id", "table_number", "led_type", "ip_address",
        "shared_folder", "enabled", "last_connection_test", "connection_status",
    ],
    "download_history": [
        "download_id", "event_id", "file_name", "table_number", "led_type",
        "source_path", "local_path", "source_timestamp", "download_timestamp",
        "status",
    ],
    "sync_history": [
        "sync_id", "event_id", "file_name", "destination", "sync_timestamp",
        "status", "error_message",
    ],
    "application_settings": ["setting_name", "setting_value"],
    "operation_log": [
        "log_id", "event_id", "operation", "timestamp", "status", "message",
    ],
    "exception_log": [
        "exception_id", "event_id", "timestamp", "operation", "category",
        "message", "resolution_status", "table_number", "led_type",
        "file_name", "source", "destination",
    ],
}
