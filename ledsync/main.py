"""Desktop entry point: init DB -> start loopback Flask server -> open native window.

Closing the window stops the server and exits the process; nothing is left running.
Any startup failure is logged to a file and shown in a dialog (the app normally
runs under pythonw, with no console).
"""

import argparse
import logging
import sys
import time

from . import APP_NAME, __version__, config, logging_setup, platform_checks
from .db import init_db
from .web import create_app, start_server

log = logging.getLogger("ledsync")

WINDOW_SIZE = (1280, 800)
WINDOW_MIN_SIZE = (1024, 640)
WINDOW_BACKGROUND = "#000000"  # --wtt-black; avoids a white flash before first paint

WEBVIEW2_HELP = (
    "The Microsoft Edge WebView2 Runtime is required but was not found.\n\n"
    "Install it from https://developer.microsoft.com/microsoft-edge/webview2/ "
    "(Evergreen Standalone Installer), then start the application again."
)


class StartupError(RuntimeError):
    """A failure whose message is safe and useful to show the operator."""


def _auto_close(window, seconds: float) -> None:
    time.sleep(seconds)
    window.destroy()


def _run(args: argparse.Namespace) -> None:
    cfg = config.load()
    log_path = logging_setup.setup_logging(cfg.data_dir)
    log.info("Starting %s v%s (data dir: %s)", APP_NAME, __version__, cfg.data_dir)
    if log_path:
        log.info("Diagnostic log: %s", log_path)

    if platform_checks.webview2_version() is None:
        raise StartupError(WEBVIEW2_HELP)

    init_db(cfg.db_path)

    # Imported lazily so that non-UI entry points (e.g. the Phase 12 scheduled
    # run) never load pythonnet/WinForms.
    import webview

    running = start_server(create_app(cfg))
    log.info("UI server listening on %s", running.url)
    try:
        window = webview.create_window(
            APP_NAME, running.url,
            width=WINDOW_SIZE[0], height=WINDOW_SIZE[1], min_size=WINDOW_MIN_SIZE,
            background_color=WINDOW_BACKGROUND,
        )
        if args.auto_close is not None:
            webview.start(_auto_close, (window, args.auto_close))
        else:
            webview.start()
    finally:
        if not running.stop():
            log.warning("UI server thread did not stop within the timeout")
        log.info("Application closed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ledsync", description=APP_NAME)
    parser.add_argument(
        "--auto-close", type=float, metavar="SECONDS", default=None,
        help="Close the window automatically after N seconds (used by automated smoke tests).",
    )
    args = parser.parse_args(argv)

    logging_setup.setup_logging(None)  # console only until the data dir is known
    try:
        _run(args)
        return 0
    except StartupError as exc:
        log.error("Startup failed: %s", exc)
        platform_checks.fatal_dialog(APP_NAME, str(exc))
    except Exception as exc:
        log.exception("Application failed")
        platform_checks.fatal_dialog(
            APP_NAME,
            f"The application could not start or stopped unexpectedly.\n\n{exc}\n\n"
            "Details have been written to the diagnostic log in the application data folder "
            "(logs\\ledsync.log).",
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
