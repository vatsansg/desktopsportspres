"""The Logs page (BRD Sections 21.1, 24 and 25): the operational log and the exception log, filterable, with the
administrator's review of exceptions and a CSV export of what is filtered.

Times are stored in UTC and shown as DD/MM/YY HH:MM:SS in this computer's local time. A date filter means that local day.
"""

import csv
import io
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, session, url_for

from .. import APP_NAME, __version__
from ..services import events as event_service, exceptions, oplog
from ..services.localchangelog import _safe as csv_safe
from .app_db import get_db
from .security import login_required

bp = Blueprint("logs", __name__, url_prefix="/logs")

TABS = ("operations", "exceptions")
MAX_EXPORT_ROWS = 50000


def _day_start(text: str, tz, plus_days: int = 0) -> str:
    """The UTC instant at which the local day `text` (YYYY-MM-DD) starts, plus `plus_days`; empty if it is not a date."""
    try:
        day = date.fromisoformat((text or "").strip()[:10]) + timedelta(days=plus_days)
        local = datetime(day.year, day.month, day.day)
        local = local.replace(tzinfo=tz) if tz is not None else local.astimezone()
        return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError, OSError):
        return ""


def _filters() -> dict:
    args = request.args
    tab = args.get("tab", "operations")
    return {
        "tab": tab if tab in TABS else "operations",
        "event": args.get("event", "")[:60].strip(),
        "kind": args.get("kind", "")[:60].strip(),              # operation (operational tab) or category (exception tab)
        "status": args.get("status", "")[:30].strip(),
        "text": args.get("text", "")[:100].strip(),
        "from": args.get("from", "")[:10].strip(),
        "to": args.get("to", "")[:10].strip(),
        "page": max(1, args.get("page", 1, type=int) or 1),
    }


def _query(f: dict, tz, *, limit: int, offset: int):
    common = dict(event_id=f["event"], status=f["status"], text=f["text"], start=_day_start(f["from"], tz),
                  end=_day_start(f["to"], tz, 1), limit=limit, offset=offset)
    if f["tab"] == "exceptions":
        return exceptions.query(get_db(), category=f["kind"], **common)
    return oplog.query(get_db(), operation=f["kind"], **common)


def _keep(f: dict, **change) -> dict:
    """The filter values as query parameters (empty ones left out), with `change` applied."""
    merged = {**f, **change}
    return {k: v for k, v in merged.items() if v not in ("", None) and not (k == "page" and v == 1)}


@bp.get("")
@login_required
def page():
    f = _filters()
    tz = current_app.config.get("DISPLAY_TZ")
    per_page = oplog.PAGE_SIZE
    rows, total = _query(f, tz, limit=per_page, offset=(f["page"] - 1) * per_page)
    pages = max(1, -(-total // per_page))
    shown = []
    for r in rows:
        item = dict(r)
        item["when"] = event_service.format_timestamp(r["timestamp"], tz, seconds=True)
        shown.append(item)
    event_ids = [r["event_id"] for r in get_db().execute("SELECT event_id FROM events ORDER BY event_id")]
    return render_template(
        "logs.html", app_name=APP_NAME, version=__version__, username=session.get("user"), f=f, rows=shown, total=total,
        pages=pages, event_ids=event_ids, kinds=(exceptions.CATEGORIES if f["tab"] == "exceptions" else oplog.OPERATIONS),
        statuses=(exceptions.STATUSES if f["tab"] == "exceptions" else oplog.STATUS_FILTERS), review_statuses=exceptions.STATUSES,
        tab_url=lambda tab: url_for("logs.page", **_keep({"event": f["event"], "from": f["from"], "to": f["to"], "text": ""}, tab=tab)),
        page_url=lambda n: url_for("logs.page", **_keep(f, page=n)),
        export_url=url_for("logs.export", **_keep(f, page=1)), keep=_keep(f, page=1))


@bp.post("/exceptions/<int:exception_id>/status")
@login_required
def review(exception_id):
    """Mark one exception Open, Acknowledged or Resolved, then return to the same filtered view."""
    status = request.form.get("new_status", "")
    if status not in exceptions.STATUSES:
        abort(400)
    if not exceptions.set_status(get_db(), exception_id, status):
        abort(404)
    flash(f"Exception {exception_id} marked {status}.", "success")
    back = {k: v for k, v in request.form.items() if k in ("event", "kind", "status", "text", "from", "to", "page") and v}
    return redirect(url_for("logs.page", tab="exceptions", **back))


def _csv(rows, columns, headers) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(headers)
    for r in rows:
        writer.writerow([csv_safe(c) for c in columns(r)])
    return chr(0xFEFF) + out.getvalue()                        # a byte-order mark so a spreadsheet reads the UTF-8 correctly


@bp.get("/export")
@login_required
def export():
    """CSV of everything the current filter matches (up to 50,000 rows), formula-neutralised."""
    f = _filters()
    tz = current_app.config.get("DISPLAY_TZ")
    rows, _ = _query(f, tz, limit=MAX_EXPORT_ROWS, offset=0)
    when = lambda r: event_service.format_timestamp(r["timestamp"], tz, seconds=True)      # noqa: E731
    if f["tab"] == "exceptions":
        body = _csv(rows, lambda r: (when(r), r["event_id"], r["table_number"], r["led_type"], r["file_name"], r["operation"],
                                     r["category"], r["message"], r["source"], r["destination"], r["resolution_status"]),
                    ("Date/Time", "Event ID", "Table", "LED Type", "File Name", "Operation", "Error Category", "Error Description",
                     "Source", "Destination", "Resolution/Status"))
        name = "ledsync_exception_log.csv"
    else:
        body = _csv(rows, lambda r: (when(r), r["event_id"], r["operation"], r["status"], r["message"]),
                    ("Date/Time", "Event ID", "Operation", "Status", "Message"))
        name = "ledsync_operational_log.csv"
    oplog.record(get_db(), "Log Export", "Success", f"{len(rows)} row(s) exported from the {f['tab']} log.")
    return Response(body, mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"',
                                                        "Cache-Control": "no-store"})
