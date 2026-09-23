"""Schema migration runner (Phase 13; resolves BRD Section 36's open "schema migration approach"
item, ahead of any concrete need - owner decision, 23 Sep 2026: build the framework now rather than
waiting for the first real structural change).

A brand-new database is always created directly from `schema.DDL`, which already reflects the
CURRENT schema, and is stamped straight to `SCHEMA_VERSION` - it never runs any migration at all.
Migrations exist only to bring an EXISTING database, created by an older version of this
application, up to date without losing its data (BRD 13.2's "preserve the existing database").

Each `Migration` advances `PRAGMA user_version` by exactly one step, applied in order, each inside
its own transaction - so a failure partway through an upgrade leaves the database at the last
successfully-completed version, never a half-applied one.
"""

import sqlite3
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Migration:
    version: int                                   # the user_version this migration produces
    description: str
    apply: Callable[[sqlite3.Connection], None]


def _v2_add_events_cutoff_columns(conn: sqlite3.Connection) -> None:
    """Phase 10 (BRD 14/18): the daily Timestamp Cut-off. Only ever runs against a database created
    before Phase 10 - a fresh database already has both columns, from schema.DDL. Column-existence
    checked directly (not just guarded by the version number) so this remains safe to re-run."""
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    if "cutoff_enabled" not in columns:
        conn.execute("ALTER TABLE events ADD COLUMN cutoff_enabled INTEGER NOT NULL DEFAULT 0")
    if "cutoff_time" not in columns:
        conn.execute("ALTER TABLE events ADD COLUMN cutoff_time TEXT")


# Ordered, one version apart, starting at 2 - version 1 was the original Phase 0 schema, from
# before this framework existed, so there is nothing to migrate a version-1 database FROM except
# this first step.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(2, "Add events.cutoff_enabled/cutoff_time (Phase 10)", _v2_add_events_cutoff_columns),
)


def run_migrations(conn: sqlite3.Connection, current_version: int, target_version: int) -> None:
    """Apply every registered migration strictly between current_version and target_version, in
    order. Never called for a brand-new database (version 0) - see the module docstring."""
    for migration in MIGRATIONS:
        if current_version < migration.version <= target_version:
            migration.apply(conn)
            conn.execute(f"PRAGMA user_version = {migration.version}")
            conn.commit()
