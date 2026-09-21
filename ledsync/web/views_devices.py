"""Event details: LED structure (Step 5.1), device/folder mapping (Step 5.2) and connection tests (Step 5.3)."""

import logging
import sqlite3

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for

from .. import APP_NAME, __version__
from ..services import connectivity, events as event_service, mappings, registration as reg, structure
from .app_db import get_db
from .security import login_required

log = logging.getLogger(__name__)
bp = Blueprint("devices", __name__, url_prefix="/events")

STATUS_CLASS = {
    mappings.STATUS_OK: "synced",
    mappings.STATUS_FAILED: "attention",
    mappings.STATUS_UNTESTED: "ready",
    mappings.STATUS_UNMAPPED: "registered",
}


def _checker():
    """Runs the folder checks. A factory on the app so tests can supply a fake."""
    return current_app.extensions["ledsync.checker"]


def _load(event_id: str):
    """(event row, canonical event id) or a branded 404. The URL is untrusted."""
    if not reg._EVENT_ID_RE.match(event_id or ""):
        abort(404)
    row = get_db().execute("SELECT event_id, event_name, status, last_updated FROM events "
                           "WHERE event_id = ? COLLATE NOCASE", (event_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def _field(kind: str, table: int, led: str) -> str:
    return f"{kind}-{table}-{led}"


def _rows(event_id: str, posted: dict | None = None):
    """Display rows for the enabled mappings. `posted` (after a rejected form) supplies what was typed."""
    tz = current_app.config.get("DISPLAY_TZ")
    out = []
    for m in mappings.list_mappings(get_db(), event_id):
        ip = posted.get(_field("ip", m.table_number, m.led_type), m.ip_address) if posted else m.ip_address
        folder = posted.get(_field("folder", m.table_number, m.led_type), m.shared_folder) if posted else m.shared_folder
        out.append(dict(
            key=m.key, table=m.table_number, led=structure.LED_LABELS.get(m.led_type, m.led_type), label=m.label,
            ip=ip, folder=folder, status=m.status, status_class=STATUS_CLASS.get(m.status, "other"),
            tested=event_service.format_timestamp(m.last_connection_test, tz) if m.last_connection_test else "",
            ip_name=_field("ip", m.table_number, m.led_type), folder_name=_field("folder", m.table_number, m.led_type),
            has_folder=bool(m.shared_folder)))
    return out


def _render(row, struct, status=200, posted=None, error=None):
    hidden = [m for m in mappings.list_mappings(get_db(), row["event_id"], enabled=False) if m.shared_folder]
    ctx = dict(app_name=APP_NAME, version=__version__, username=session.get("user"), event=row,
               structure=struct, structure_error=None, rows=_rows(row["event_id"], posted), hidden=hidden,
               led_types=structure.LED_TYPES, led_labels=structure.LED_LABELS, error=error)
    return render_template("event_details.html", **ctx), status


@bp.get("/<event_id>")
@login_required
def details(event_id):
    row = _load(event_id)
    try:
        struct = structure.load_structure(get_db(), row["event_id"])
    except structure.StructureError as err:
        return render_template("event_details.html", app_name=APP_NAME, version=__version__,
                               username=session.get("user"), event=row, structure=None, structure_error=str(err),
                               rows=[], hidden=[], led_types=structure.LED_TYPES, led_labels=structure.LED_LABELS,
                               error=None)
    try:
        mappings.ensure_rows(get_db(), row["event_id"], struct)    # idempotent: creates any missing rows
    except sqlite3.Error:
        log.warning("Could not create the mapping rows for an event.")   # the page still opens with what exists
        get_db().rollback()
    return _render(row, struct)


def _entries(struct) -> dict:
    """The typed values, for enabled LEDs ONLY - a forged field for any other table/LED is never read."""
    form, out = request.form, {}
    for table, led in struct.enabled_pairs:
        ip_key, folder_key = _field("ip", table, led), _field("folder", table, led)
        if ip_key in form or folder_key in form:
            out[(table, led)] = (form.get(ip_key, "")[:60], form.get(folder_key, "")[:mappings.MAX_PATH_LENGTH + 10])
    return out


@bp.post("/<event_id>/mappings")
@login_required
def save_or_test(event_id):
    row = _load(event_id)
    db = get_db()
    try:
        struct = structure.load_structure(db, row["event_id"])
    except structure.StructureError:
        abort(400)
    action = request.form.get("action", "save")
    # Reject a forged action BEFORE anything is saved.
    valid_tests = {f"test:{table}-{led}" for table, led in struct.enabled_pairs}
    if action not in ("save", "test-all") and action not in valid_tests:
        abort(400)
    data_dir = current_app.config["LEDSYNC"].data_dir

    try:
        changed = mappings.save_mappings(db, row["event_id"], struct, _entries(struct), forbidden_roots=(data_dir,))
    except mappings.MappingError as err:
        return _render(row, struct, 400, posted=request.form, error=str(err))

    if action == "save":
        flash("Device mapping saved." if changed else "Nothing changed — the mapping was already saved.",
              "success" if changed else "info")
        return redirect(url_for("devices.details", event_id=row["event_id"]))

    return _run_tests(row, action)


def _run_tests(row, action):
    db = get_db()
    everything = mappings.list_mappings(db, row["event_id"])
    if action == "test-all":
        targets = [m for m in everything if m.shared_folder]
        skipped = [m for m in everything if not m.shared_folder]
        if not targets:
            flash("There is nothing to test yet — enter a shared folder for at least one destination.", "info")
            return redirect(url_for("devices.details", event_id=row["event_id"]))
    elif action.startswith("test:"):
        wanted = action[5:]
        targets = [m for m in everything if m.key == wanted]
        if not targets:
            abort(400)
        skipped = []
        if not targets[0].shared_folder:
            flash(f"{targets[0].label} has no shared folder to test yet.", "info")
            return redirect(url_for("devices.details", event_id=row["event_id"]))
    else:
        abort(400)

    data_dir = current_app.config["LEDSYNC"].data_dir
    results = _checker()({m.mapping_id: m.shared_folder for m in targets}, forbidden_roots=(data_dir,))
    ok_count = 0
    for m in targets:
        result = results[m.mapping_id]
        try:
            recorded = mappings.record_test(db, m, result.ok, result.message, result.category, result.warning)
        except mappings.MappingError as err:
            flash(str(err), "error")
            return redirect(url_for("devices.details", event_id=row["event_id"]))
        if not recorded:
            flash(f"{m.label}: the folder was changed while it was being tested. Run the test again.", "info")
            continue
        if result.ok:
            ok_count += 1
            if result.warning:
                flash(f"{m.label}: {result.warning}", "info")
        else:
            flash(f"{m.label}: Connection Failed — {result.message}", "error")
    total = len(targets)
    summary = (f"{ok_count} of {total} destination{'s' if total != 1 else ''} passed: Connection Successful."
               if ok_count == total else f"{ok_count} of {total} destination{'s' if total != 1 else ''} passed.")
    flash(summary, "success" if ok_count == total else "info")
    if skipped:
        flash(f"{len(skipped)} destination{'s have' if len(skipped) != 1 else ' has'} no folder and "
              f"{'were' if len(skipped) != 1 else 'was'} not tested.", "info")
    return redirect(url_for("devices.details", event_id=row["event_id"]))
