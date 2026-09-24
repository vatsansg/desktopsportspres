"""PyInstaller entry point for the headless scheduled-run executable (Phase 13).

A SEPARATE frozen executable from run_gui.py (see services/scheduler.py's SCHEDULED_RUN_EXE_NAME
comment) - built from its own Analysis so this binary's import graph can never pull in pywebview,
matching ledsync/scheduled_run.py's own "no GUI toolkit" guarantee for the real shipped artifact,
not just the source layout.
"""

import sys

from ledsync.scheduled_run import main

if __name__ == "__main__":
    sys.exit(main())
