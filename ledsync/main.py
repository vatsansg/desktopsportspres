"""Desktop entry point: init DB -> start loopback Flask server -> open native window.

Closing the window stops the server and exits the process; nothing is left running.
Any startup failure is logged to a file and shown in a dialog (the app normally
runs under pythonw, with no console).
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from . import APP_NAME, __version__, config, logging_setup, platform_checks
from .db import connect, init_db
from .services import auth, exceptions, oplog
from .services import settings as cloud_settings
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


def _prune_logs(conn) -> None:
    """Settings -> Application's log retention (Phase 10, carried from Phase 9 F-65); blank = keep forever, the
    default. Runs before the startup row is written, so a fresh install's very first row is never itself pruned."""
    retention = cloud_settings.load_log_retention(conn)
    if retention.days is None:
        return
    before = (datetime.now(timezone.utc) - timedelta(days=retention.days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    removed = oplog.prune(conn, before) + exceptions.prune(conn, before)
    if removed:
        oplog.record(conn, "Application Startup", "Success",
                     f"Log retention: removed {removed} row(s) older than {retention.days} day(s).")


def _seed_and_log_startup(cfg: config.Config) -> None:
    conn = connect(cfg.db_path)
    try:
        auth.seed_admin(conn)
        _prune_logs(conn)
        oplog.record(conn, "Application Startup", "Success", f"{APP_NAME} v{__version__} started.")
    finally:
        conn.close()


def _run(args: argparse.Namespace) -> None:
    cfg = config.load()
    log_path = logging_setup.setup_logging(cfg.data_dir)
    log.info("Starting %s v%s (data dir: %s)", APP_NAME, __version__, cfg.data_dir)
    if log_path:
        log.info("Diagnostic log: %s", log_path)

    if platform_checks.webview2_version() is None:
        raise StartupError(WEBVIEW2_HELP)

    init_db(cfg.db_path)
    _seed_and_log_startup(cfg)

    # Imported lazily so that non-UI entry points (e.g. the Phase 12 scheduled
    # run) never load pythonnet/WinForms.
    import webview

    # pywebview refuses every download by default (WebView2 cancels it before it starts, with no
    # error and no file). The Logs page's CSV export needs it: allowing it makes WebView2 show its
    # native Save As dialog (defaulting to Downloads), which is also the operator's confirmation
    # that the export happened and where it went.
    webview.settings["ALLOW_DOWNLOADS"] = True

    running = start_server(create_app(cfg))
    log.info("UI server listening on %s", running.url)  # never log launch_url: it carries the one-time token
    try:
        window = webview.create_window(
            APP_NAME, running.launch_url,
            width=WINDOW_SIZE[0], height=WINDOW_SIZE[1], min_size=WINDOW_MIN_SIZE,
            background_color=WINDOW_BACKGROUND,
            # Venue laptops can be small (a 1280x800 panel has only ~752px of usable
            # height once the taskbar is counted). Maximised always fits the screen.
            maximized=True,
        )
        if args.auto_close is not None:
            webview.start(_auto_close, (window, args.auto_close), private_mode=True)
        else:
            # private_mode: no cookies/form data/password autofill persisted by WebView2.
            webview.start(private_mode=True)
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
