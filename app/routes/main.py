"""Page routes: dashboard, expense list, add/edit/delete, undo, export and import."""
import csv
import io
import json
from datetime import date
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from flask import (
    Blueprint,
    Response,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app import db
from app import stats as s
from app.forms import read_expense_form, safe_next_url
from app.models import ChangeLog, Expense
from app.parser import ALL_CATEGORIES, get_category, import_csv
from app.translations import SUPPORTED_LANGS, format_eur, t

main = Blueprint("main", __name__)

# date() only accepts years in this range; query params outside it are ignored.
_MIN_YEAR = 1
_MAX_YEAR = 9999

_EXPENSES_PER_PAGE = 50
_UNDO_HISTORY_LIMIT = 5
_DASHBOARD_RECENT_LIMIT = 10
_DASHBOARD_TOP_STORES = 8
_STATS_TOP_MONTHS = 5
_STATS_TRIP_LOOKBACK_DAYS = 365

_EXPENSE_FIELDS = ("date", "store", "store_detail", "amount", "category", "notes")


def _safe_int(value, default: int | None = None) -> int | None:
    """Parse an int from user input, returning ``default`` instead of raising."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _filter_args() -> dict[str, str]:
    """The expense list filters from the query string (shared by list and export)."""
    return {
        "year": request.args.get("year", ""),
        "month": request.args.get("month", ""),
        "category": request.args.get("category", ""),
        "store": request.args.get("store", ""),
        "search": request.args.get("search", "").strip(),
    }


def _apply_expense_filters(q, year: str, month: str, category: str, store: str, search: str):
    """Apply the expense list/export filters to an Expense query.

    Non-numeric or out-of-range year/month values are ignored rather than
    raising, so hand-edited URLs degrade gracefully instead of returning 500.
    """
    y = _safe_int(year)
    m = _safe_int(month)
    if y is not None and not _MIN_YEAR <= y <= _MAX_YEAR:
        y = None
    if m is not None and not 1 <= m <= 12:
        m = None

    if y and m:
        p_start, p_end = s.period_bounds(y, m)
        q = q.filter(Expense.date >= p_start, Expense.date <= p_end)
    elif y:
        y_start, y_end = s.year_bounds(y)
        q = q.filter(Expense.date >= y_start, Expense.date <= y_end)
    if category:
        q = q.filter(Expense.category == category)
    if store:
        # The dropdown shows canonical names: match every raw name aliased to it,
        # in both the store and the legacy store_detail column.
        raw_names = {raw for raw, can in s.get_alias_map().items() if can == store} | {store}
        q = q.filter(db.or_(Expense.store.in_(raw_names), Expense.store_detail.in_(raw_names)))
    if search:
        like = f"%{search}%"
        q = q.filter(
            db.or_(
                Expense.store.ilike(like),
                Expense.store_detail.ilike(like),
                Expense.notes.ilike(like),
            )
        )
    return q


def _expense_snapshot(expense: Expense) -> dict:
    """JSON-serialisable copy of the editable fields, stored in the undo log."""
    snap = {field: getattr(expense, field) for field in _EXPENSE_FIELDS}
    snap["date"] = expense.date.isoformat() if expense.date else None
    return snap


def _flash_errors(error_keys: list[str]) -> None:
    for key in error_keys:
        flash(t(key), "danger")


# ── Dashboard & statistics ────────────────────────────────────────────────────

@main.route("/")
def dashboard():
    """Current (or selected) budget period: KPIs, forecast, charts, recent entries."""
    today = date.today()
    cur_year, cur_month = s.period_for_date(today)
    year = _safe_int(request.args.get("year"), cur_year)
    month = _safe_int(request.args.get("month"), cur_month)
    if not _MIN_YEAR <= year <= _MAX_YEAR:
        year = cur_year
    if not 1 <= month <= 12:
        month = cur_month

    p_start, p_end = s.period_bounds(year, month)
    is_current_month = (year, month) == (cur_year, cur_month)
    available_years = s.get_available_years()

    recent = (
        Expense.query.filter(Expense.date >= p_start, Expense.date <= p_end)
        .order_by(Expense.date.desc())
        .limit(_DASHBOARD_RECENT_LIMIT)
        .all()
    )

    # Day counts refer to the budget period, not the calendar month.
    days_in_period = (p_end - p_start).days + 1
    days_elapsed = (today - p_start).days + 1 if is_current_month else days_in_period

    show_yoy = len(available_years) > 1 and year > _MIN_YEAR

    return render_template(
        "dashboard.html",
        summary=s.get_monthly_summary(year, month),
        categories=s.get_category_breakdown(year, month),
        top_stores=s.get_store_breakdown(year, month, limit=_DASHBOARD_TOP_STORES),
        recent=recent,
        available_years=available_years,
        selected_year=year,
        selected_month=month,
        has_data=db.session.query(Expense.id).first() is not None,
        transaction_count=s.get_monthly_transaction_count(year, month),
        prediction=s.get_prediction(year, month),
        is_current_month=is_current_month,
        yoy_summary=s.get_monthly_summary(year - 1, month) if show_yoy else None,
        days_in_period=days_in_period,
        days_elapsed=days_elapsed,
    )


@main.route("/statistics")
def statistics():
    """All-time KPIs, best/worst periods and shopping intervals."""
    return render_template(
        "statistics.html",
        overall=s.get_overall_stats(),
        top_worst=s.get_top_months(limit=_STATS_TOP_MONTHS, best=False),
        top_best=s.get_top_months(limit=_STATS_TOP_MONTHS, best=True),
        grocery_stats=s.trip_interval_stats("Lebensmittel", _STATS_TRIP_LOOKBACK_DAYS),
        fuel_stats=s.trip_interval_stats("Tanken", _STATS_TRIP_LOOKBACK_DAYS),
        available_years=s.get_available_years(),
    )


# ── Expense list, export ──────────────────────────────────────────────────────

@main.route("/expenses")
def expenses():
    """Paginated, filterable expense table with edit modal and undo history."""
    page = max(1, _safe_int(request.args.get("page"), 1))
    filters = _filter_args()

    q = _apply_expense_filters(Expense.query, **filters)
    total_amount = q.with_entities(db.func.sum(Expense.amount)).scalar() or 0.0
    q = q.order_by(Expense.date.desc().nullslast(), Expense.id.desc())
    pagination = q.paginate(page=page, per_page=_EXPENSES_PER_PAGE, error_out=False)

    # Set by edit_expense when a category change could apply to other entries.
    bulk_prompt = None
    bulk_keys = ("store", "category", "old_category", "count")
    bulk_values = {k: request.args.get(f"bulk_{k}", "") for k in bulk_keys}
    if all(bulk_values.values()):
        bulk_prompt = bulk_values

    recent_logs = (
        ChangeLog.query.filter_by(undone=False)
        .order_by(ChangeLog.timestamp.desc())
        .limit(_UNDO_HISTORY_LIMIT)
        .all()
    )

    return render_template(
        "expenses.html",
        expenses=pagination.items,
        pagination=pagination,
        available_years=s.get_available_years(),
        all_stores=s.get_distinct_stores(),
        all_categories=ALL_CATEGORIES,
        selected_year=filters["year"],
        selected_month=filters["month"],
        selected_category=filters["category"],
        selected_store=filters["store"],
        search=filters["search"],
        total_amount=round(total_amount, 2),
        bulk_prompt=bulk_prompt,
        recent_logs=recent_logs,
        anomaly_map=s.get_store_anomaly_thresholds(),
    )


@main.route("/expenses/export")
def export_expenses():
    """Download the currently filtered expenses as CSV (default) or JSON."""
    q = _apply_expense_filters(Expense.query, **_filter_args())
    rows = q.order_by(Expense.date.desc().nullslast(), Expense.id.desc()).all()

    if request.args.get("format") == "json":
        return Response(
            json.dumps([e.to_dict() for e in rows], ensure_ascii=False, indent=2),
            mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=expenses.json"},
        )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(_EXPENSE_FIELDS)
    for e in rows:
        writer.writerow([
            e.date.isoformat() if e.date else "",
            e.store,
            e.store_detail or "",
            e.amount,
            e.category or "",
            e.notes or "",
        ])
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=expenses.csv"},
    )


# ── Add / edit / delete ───────────────────────────────────────────────────────

@main.route("/add", methods=["GET", "POST"])
def add_expense():
    """Manual entry form (plus the QR code for phone scanning)."""
    if request.method == "POST":
        values, errors = read_expense_form(request.form)
        if errors:
            _flash_errors(errors)
            return redirect(url_for("main.add_expense"))

        values["category"] = get_category(values["store"], values["store_detail"])
        db.session.add(Expense(**values))
        db.session.commit()
        flash(t("flash_expense_added", format_eur(values["amount"]), values["store"]), "success")
        return redirect(url_for("main.dashboard"))

    return render_template("add_expense.html")


@main.route("/expense/<int:expense_id>/edit", methods=["POST"])
def edit_expense(expense_id: int):
    """Save the edit modal, log the change and offer a bulk category update."""
    expense = db.get_or_404(Expense, expense_id)
    next_url = safe_next_url(request.form.get("_next"), url_for("main.expenses"))

    values, errors = read_expense_form(request.form)
    if errors:
        _flash_errors(errors)
        return redirect(next_url)

    old = _expense_snapshot(expense)
    old_display = expense.display_store

    category = request.form.get("category", "").strip()
    if category not in ALL_CATEGORIES:  # empty means "auto-detect"
        category = get_category(values["store"], values["store_detail"])
    for field, value in values.items():
        setattr(expense, field, value)
    expense.category = category

    if old["category"] != category:
        description = f"Edited {expense.store}: {old['category']} → {category}"
    else:
        description = f"Edited {expense.store} (amount/date/notes)"
    db.session.add(ChangeLog(
        op_type="edit",
        description=description,
        payload_json=json.dumps(
            {"expense_id": expense_id, "old": old, "new": _expense_snapshot(expense)}
        ),
    ))
    db.session.commit()
    flash(t("flash_entry_updated"), "success")

    # Offer a bulk update when only the category changed and other entries of
    # the same store are still in the old category. Matching uses display_store
    # against both columns so legacy "Sonstige"+detail rows are included.
    if old["category"] != category and expense.display_store == old_display:
        others = (
            Expense.query
            .filter(db.or_(Expense.store == old_display, Expense.store_detail == old_display))
            .filter(Expense.category == old["category"], Expense.id != expense_id)
            .count()
        )
        if others:
            parsed = urlparse(next_url)
            params = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
            params.update({
                "bulk_store": old_display,
                "bulk_category": category,
                "bulk_old_category": old["category"],
                "bulk_count": str(others),
            })
            return redirect(urlunparse(parsed._replace(query=urlencode(params))))

    return redirect(next_url)


@main.route("/expense/bulk-category", methods=["POST"])
def bulk_category():
    """Move every entry of a store from one category to another (undoable)."""
    store = request.form.get("store", "").strip()
    category = request.form.get("category", "").strip()
    old_category = request.form.get("old_category", "").strip()
    next_url = safe_next_url(request.form.get("_next"), url_for("main.expenses"))

    if not store or not old_category or category not in ALL_CATEGORIES:
        flash(t("flash_invalid_request"), "danger")
        return redirect(next_url)

    # Only entries still in the OLD category are touched.
    targets = (
        Expense.query
        .filter(db.or_(Expense.store == store, Expense.store_detail == store))
        .filter(Expense.category == old_category)
        .all()
    )
    for e in targets:
        e.category = category
    affected_ids = [e.id for e in targets]

    db.session.add(ChangeLog(
        op_type="bulk",
        description=f"Bulk: {store} {old_category} → {category} ({len(affected_ids)} entries)",
        payload_json=json.dumps({
            "store": store,
            "old_category": old_category,
            "new_category": category,
            "affected_ids": affected_ids,
        }),
    ))
    db.session.commit()
    flash(t("flash_bulk_updated", len(affected_ids), store, old_category, category), "success")
    return redirect(next_url)


def _affected_expense_ids(log: ChangeLog) -> set[int]:
    """IDs of the expenses a logged operation changed."""
    payload = log.payload
    if log.op_type == "bulk":
        return set(payload.get("affected_ids", []))
    expense_id = payload.get("expense_id")
    return {expense_id} if expense_id is not None else set()


def _has_newer_conflicting_change(log: ChangeLog) -> bool:
    """Whether a later, still active log entry touched any of the same expenses.

    Undoing an older change in that case would silently overwrite the newer one,
    so changes must be undone newest first per entry.
    """
    ids = _affected_expense_ids(log)
    newer = ChangeLog.query.filter(ChangeLog.id > log.id, ChangeLog.undone.is_(False)).all()
    return any(ids & _affected_expense_ids(other) for other in newer)


@main.route("/expense/undo/<int:log_id>", methods=["POST"])
def undo_change(log_id: int):
    """Reverse one logged edit, bulk category change or deletion."""
    next_url = safe_next_url(request.form.get("_next"), url_for("main.expenses"))
    log = db.get_or_404(ChangeLog, log_id)

    if log.undone:
        flash(t("flash_already_undone"), "warning")
        return redirect(next_url)
    if _has_newer_conflicting_change(log):
        flash(t("flash_undo_blocked"), "warning")
        return redirect(next_url)

    payload = log.payload
    if log.op_type == "delete":
        old = payload["old"]
        restored = Expense(
            date=date.fromisoformat(old["date"]) if old.get("date") else None,
            **{field: old.get(field) for field in _EXPENSE_FIELDS if field != "date"},
        )
        # Keep the original ID unless SQLite has handed it to a new row since.
        if db.session.get(Expense, payload["expense_id"]) is None:
            restored.id = payload["expense_id"]
        db.session.add(restored)
        message = t("flash_undo_edit")

    elif log.op_type == "edit":
        expense_id = payload.get("expense_id")
        expense = db.session.get(Expense, expense_id) if expense_id is not None else None
        if expense is None:
            flash(t("flash_entry_missing"), "danger")
            return redirect(next_url)
        old = payload["old"]
        for field in _EXPENSE_FIELDS:
            if field != "date":
                setattr(expense, field, old.get(field))
        expense.date = date.fromisoformat(old["date"]) if old.get("date") else None
        message = t("flash_undo_edit")

    elif log.op_type == "bulk":
        reverted = 0
        for eid in payload.get("affected_ids", []):
            e = db.session.get(Expense, eid)
            # Skip entries that were re-categorised again in the meantime.
            if e and e.category == payload["new_category"]:
                e.category = payload["old_category"]
                reverted += 1
        message = t("flash_undo_bulk", reverted, payload["old_category"])

    else:
        flash(t("flash_invalid_request"), "danger")
        return redirect(next_url)

    log.undone = True
    db.session.commit()
    flash(message, "success")
    return redirect(next_url)


@main.route("/expense/<int:expense_id>/delete", methods=["POST"])
def delete_expense(expense_id: int):
    """Delete a single expense, keeping a snapshot in the undo log."""
    expense = db.get_or_404(Expense, expense_id)
    db.session.add(ChangeLog(
        op_type="delete",
        description=f"Deleted {expense.display_store} ({expense.amount:.2f})",
        payload_json=json.dumps({"expense_id": expense_id, "old": _expense_snapshot(expense)}),
    ))
    db.session.delete(expense)
    db.session.commit()
    flash(t("flash_expense_deleted"), "success")
    return redirect(safe_next_url(request.referrer, url_for("main.expenses")))


# ── Misc ──────────────────────────────────────────────────────────────────────

@main.route("/lang/<code>")
def set_lang(code: str):
    """Switch the UI language and return to the previous page."""
    if code in SUPPORTED_LANGS:
        session["lang"] = code
    return redirect(safe_next_url(request.referrer, url_for("main.dashboard")))


@main.route("/import", methods=["GET", "POST"])
def import_page():
    """Import the configured CSV. Appends and de-duplicates unless replace is confirmed."""
    result = None
    if request.method == "POST":
        # "replace" wipes the expense table and requires an explicit checkbox.
        replace = (
            request.form.get("mode") == "replace"
            and request.form.get("confirm_replace") == "yes"
        )
        result = import_csv(current_app.config["CSV_PATH"], clear_existing=replace)
        result["mode"] = "replace" if replace else "append"
    return render_template("import.html", result=result)
