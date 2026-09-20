"""Diagnostic file logging.

This is the developer/support log (startup failures, stack traces). It is
separate from the BRD Section 25 operational log and Section 21.1 exception log,
which are business records kept in SQLite (Phase 9).
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_FILENAME = "ledsync.log"


def log_dir(data_dir: Path) -> Path:
    return data_dir / "logs"


def setup_logging(data_dir: Path | None = None) -> Path | None:
    """Configure root logging. Returns the log file path if file logging is active.

    Under pythonw there is no stderr, so the console handler is added only when
    one exists. Never raises: logging problems must not prevent startup."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Werkzeug's per-request access log would record the one-time launch URL
    # (web/security.py). Warnings and errors are still logged.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    for h in list(root.handlers):
        root.removeHandler(h)

    if sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(console)

    if data_dir is None:
        return None
    try:
        directory = log_dir(data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / LOG_FILENAME
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(handler)
        return path
    except OSError:
        return None
