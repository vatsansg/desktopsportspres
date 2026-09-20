"""Per-request SQLite connection helper."""

import sqlite3

from flask import current_app, g

from ..db import connect


def get_db() -> sqlite3.Connection:
    """Per-request SQLite connection (closed automatically at request end)."""
    if "db" not in g:
        g.db = connect(current_app.config["LEDSYNC"].db_path)
    return g.db


def close_db(_exc=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()
