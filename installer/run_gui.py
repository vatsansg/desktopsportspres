"""PyInstaller entry point for the windowed application (Phase 13).

A plain script, not `-m ledsync` - PyInstaller analyses a script's own import graph, not a package
run via `-m`. Otherwise identical to `ledsync/__main__.py`.
"""

import sys

from ledsync.main import main

if __name__ == "__main__":
    sys.exit(main())
