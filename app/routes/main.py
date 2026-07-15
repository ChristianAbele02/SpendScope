import json
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app import db
from app import stats as s
from app.models import ChangeLog, Expense
from app.parser import ALL_CATEGORIES, get_category

main = Blueprint("main", __name__)

# date() only accepts years in this range; query params outside it are ignored.
_MIN_YEAR = 1
_MAX_YEAR = 9999


def _safe_int(value, default: int | None = None) -> int | None:
    """Parse an int from user input, returning ``default`` instead of raising."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _apply_expense_filters(q, year: str, month: str, category: str, store: str, search: str):
    """Apply the shared expense list/export filters to an Expense query.

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
        y_start, y_end = s._year_bounds(y)
        q = q.filter(Expense.date >= y_start, Expense.date <= y_end)
    if category:
        q = q.filter(Expense.category == category)
    if store:
        # Match canonical store name against both raw store and store_detail columns,
        # also resolving any aliases that point to the same canonical name.
        alias_map = s.get_alias_map()
        raw_names = {raw for raw, can in alias_map.items() if can == store}
        raw_names.add(store)
        q = q.filter(
            db.or_(Expense.store.in_(raw_names), Expense.store_detail.in_(raw_names))
        )
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


@main.route("/")
def dashboard():
    today = date.today()
    default_year, default_month = s.period_for_date(today)
    year = _safe_int(request.args.get("year"), default_year)
    month = _safe_int(request.args.get("month"), default_month)
    if not _MIN_YEAR <= year <= _MAX_YEAR:
        year = default_year
    if not 1 <= month <= 12:
        month = default_month

    summary = s.get_monthly_summary(year, month)
    categories = s.get_category_breakdown(year, month)
    top_stores = s.get_store_breakdown(year, month, limit=8)
    transaction_count = s.get_monthly_transaction_count(year, month)
    prediction = s.get_prediction(year, month)

    p_start, p_end = s.period_bounds(year, month)
    recent = (
        Expense.query.filter(
            Expense.date.isnot(None),
            Expense.date >= p_start,
            Expense.date <= p_end,
        )
        .order_by(Expense.date.desc())
        .limit(10)
        .all()
    )
    available_years = s.get_available_years()
    has_data = Expense.query.count() > 0
    cur_year, cur_month = s.period_for_date(today)
    is_current_month = (year == cur_year and month == cur_month)

    # Day counts for the *budget period* (not the calendar month), so the
    # dashboard is correct when PERIOD_START_DAY != 1.
    days_in_period = (p_end - p_start).days + 1
    days_elapsed = (today - p_start).days + 1 if is_current_month else days_in_period

    # Year-over-year: same period last year
    yoy_summary = s.get_monthly_summary(year - 1, month) if len(available_years) > 1 else None

    return render_template(
        "dashboard.html",
        summary=summary,
        categories=categories,
        top_stores=top_stores,
        recent=recent,
        available_years=available_years,
        selected_year=year,
        selected_month=month,
        has_data=has_data,
        transaction_count=transaction_count,
        prediction=prediction,
        is_current_month=is_current_month,
        yoy_summary=yoy_summary,
        days_in_period=days_in_period,
        days_elapsed=days_elapsed,
    )


@main.route("/expenses")
def expenses():
    page = max(1, _safe_int(request.args.get("page"), 1))
    per_page = 50

    year = request.args.get("year", "")
    month = request.args.get("month", "")
    category = request.args.get("category", "")
    store = request.args.get("store", "")
    search = request.args.get("search", "").strip()

    q = _apply_expense_filters(Expense.query, year, month, category, store, search)

    total_amount = q.with_entities(
        db.func.sum(Expense.amount)
    ).scalar() or 0.0

    q = q.order_by(Expense.date.desc().nullslast(), Expense.id.desc())
    pagination = q.paginate(page=page, per_page=per_page, error_out=False)

    available_years = s.get_available_years()
    all_stores = s.get_distinct_stores()
    anomaly_map = s.get_store_anomaly_thresholds()

    # Bulk-category prompt (set after an edit that changed category)
    bulk_prompt = None
    bp_store        = request.args.get("bulk_store", "")
    bp_category     = request.args.get("bulk_category", "")
    bp_old_category = request.args.get("bulk_old_category", "")
    bp_count        = request.args.get("bulk_count", "")
    if bp_store and bp_category and bp_old_category and bp_count:
        bulk_prompt = {
            "store":        bp_store,
            "category":     bp_category,
            "old_category": bp_old_category,
            "count":        bp_count,
        }

    # Recent undoable operations (last 5, not yet undone)
    recent_logs = (ChangeLog.query
                   .filter_by(undone=False)
                   .order_by(ChangeLog.timestamp.desc())
                   .limit(5)
                   .all())

    return render_template(
        "expenses.html",
        expenses=pagination.items,
        pagination=pagination,
        available_years=available_years,
        all_stores=all_stores,
        all_categories=ALL_CATEGORIES,
        selected_year=year,
        selected_month=month,
        selected_category=category,
        selected_store=store,
        search=search,
        total_amount=round(total_amount, 2),
        bulk_prompt=bulk_prompt,
        recent_logs=recent_logs,
        anomaly_map=anomaly_map,
    )


@main.route("/statistics")
def statistics():
    overall = s.get_overall_stats()
    top_worst = s.get_top_months(limit=5, best=False)
    top_best = s.get_top_months(limit=5, best=True)
    grocery_stats = s._trip_interval_stats("Lebensmittel", lookback_days=365)
    fuel_stats = s._trip_interval_stats("Tanken", lookback_days=365)
    available_years = s.get_available_years()

    return render_template(
        "statistics.html",
        overall=overall,
        top_worst=top_worst,
        top_best=top_best,
        grocery_stats=grocery_stats,
        fuel_stats=fuel_stats,
        available_years=available_years,
    )


@main.route("/add", methods=["GET", "POST"])
def add_expense():
    if request.method == "POST":
        date_str = request.form.get("date", "").strip()
        store = request.form.get("store", "").strip()
        store_detail = request.form.get("store_detail", "").strip() or None
        amount_str = request.form.get("amount", "").strip()
        notes = request.form.get("notes", "").strip() or None

        errors = []
        if not store:
            errors.append("Store is required.")
        try:
            amount = float(amount_str.replace(",", "."))
        except ValueError:
            errors.append("Invalid amount.")
            amount = None

        date_val = None
        if date_str:
            try:
                date_val = date.fromisoformat(date_str)
            except ValueError:
                errors.append("Invalid date.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return redirect(url_for("main.add_expense"))

        expense = Expense(
            date=date_val,
            store=store,
            store_detail=store_detail,
            amount=amount,
            category=get_category(store, store_detail),
            notes=notes,
        )
        db.session.add(expense)
        db.session.commit()
        flash(f"Expense of €{amount:.2f} at {store} added.", "success")
        return redirect(url_for("main.dashboard"))

    return render_template(
        "add_expense.html",
        today=date.today().isoformat(),
        all_categories=ALL_CATEGORIES,
    )


@main.route("/expense/<int:expense_id>/edit", methods=["POST"])
def edit_expense(expense_id):
    expense = Expense.query.get_or_404(expense_id)

    # Snapshot old state for undo log
    old = {
        "date":         expense.date.isoformat() if expense.date else None,
        "store":        expense.store,
        "store_detail": expense.store_detail,
        "amount":       expense.amount,
        "category":     expense.category,
        "notes":        expense.notes,
    }
    old_category = expense.category
    old_display  = expense.display_store   # "OBI" whether stored as store or store_detail

    date_str     = request.form.get("date", "").strip()
    store        = request.form.get("store", "").strip()
    store_detail = request.form.get("store_detail", "").strip() or None
    amount_str   = request.form.get("amount", "").strip()
    category     = request.form.get("category", "").strip()
    notes        = request.form.get("notes", "").strip() or None
    next_url     = request.form.get("_next") or url_for("main.expenses")

    if not store:
        flash("Store is required.", "danger")
        return redirect(next_url)

    try:
        amount = round(float(amount_str.replace(",", ".")), 2)
    except ValueError:
        flash("Invalid amount.", "danger")
        return redirect(next_url)

    date_val = None
    if date_str:
        try:
            date_val = date.fromisoformat(date_str)
        except ValueError:
            flash("Invalid date.", "danger")
            return redirect(next_url)

    expense.date         = date_val
    expense.store        = store
    expense.store_detail = store_detail
    expense.amount       = amount
    expense.category     = category if category else get_category(store, store_detail)
    expense.notes        = notes

    new = {
        "date":         expense.date.isoformat() if expense.date else None,
        "store":        expense.store,
        "store_detail": expense.store_detail,
        "amount":       expense.amount,
        "category":     expense.category,
        "notes":        expense.notes,
    }

    # Log the single-entry edit
    log = ChangeLog(
        op_type="edit",
        description=f"Edited {store}: {old_category} → {expense.category}" if old_category != expense.category
                    else f"Edited {store} (amount/date/notes)",
        payload_json=json.dumps({"expense_id": expense_id, "old": old, "new": new}),
    )
    db.session.add(log)
    db.session.commit()
    flash("Entry updated.", "success")

    # Offer bulk update only if:
    # 1. Category changed
    # 2. Store name itself didn't change
    # 3. There are OTHER entries for this store that are STILL in the OLD category
    category_changed   = (expense.category != old_category)
    # The store field now round-trips the raw store column, so compare the
    # recomputed display name (store_detail or store) against the old one.
    display_unchanged  = (expense.display_store == old_display)
    if category_changed and display_unchanged:
        others = (Expense.query
                  .filter(db.or_(Expense.store == old_display,
                                 Expense.store_detail == old_display))
                  .filter(Expense.category == old_category)
                  .filter(Expense.id != expense_id)
                  .count())
        if others > 0:
            from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
            parsed = urlparse(next_url)
            params  = parse_qs(parsed.query, keep_blank_values=True)
            params["bulk_store"]        = [old_display]
            params["bulk_category"]     = [expense.category]
            params["bulk_old_category"] = [old_category]
            params["bulk_count"]        = [str(others)]
            flat    = {k: v[0] for k, v in params.items()}
            return redirect(urlunparse(parsed._replace(query=urlencode(flat))))

    return redirect(next_url)


@main.route("/expense/bulk-category", methods=["POST"])
def bulk_category():
    store        = request.form.get("store", "").strip()
    category     = request.form.get("category", "").strip()
    old_category = request.form.get("old_category", "").strip()
    next_url     = request.form.get("_next") or url_for("main.expenses")

    if not store or not category or not old_category:
        flash("Invalid request.", "danger")
        return redirect(next_url)

    # Match entries stored either as store="OBI" or store="Sonstige"+store_detail="OBI"
    targets = (Expense.query
               .filter(db.or_(Expense.store == store,
                              Expense.store_detail == store))
               .filter(Expense.category == old_category)
               .all())
    affected_ids = [e.id for e in targets]

    for e in targets:
        e.category = category

    log = ChangeLog(
        op_type="bulk",
        description=f"Bulk: {store} {old_category} → {category} ({len(affected_ids)} entries)",
        payload_json=json.dumps({
            "store":        store,
            "old_category": old_category,
            "new_category": category,
            "affected_ids": affected_ids,
        }),
    )
    db.session.add(log)
    db.session.commit()
    flash(f"Updated {len(affected_ids)} '{store}' entries: {old_category} → {category}.", "success")
    return redirect(next_url)


@main.route("/expense/undo/<int:log_id>", methods=["POST"])
def undo_change(log_id):
    next_url = request.form.get("_next") or url_for("main.expenses")
    log = ChangeLog.query.get_or_404(log_id)

    if log.undone:
        flash("Already undone.", "warning")
        return redirect(next_url)

    payload = log.payload

    if log.op_type == "edit":
        expense = Expense.query.get(payload["expense_id"])
        if expense is None:
            flash("Entry no longer exists.", "danger")
            return redirect(next_url)
        old = payload["old"]
        expense.date         = date.fromisoformat(old["date"]) if old.get("date") else None
        expense.store        = old["store"]
        expense.store_detail = old.get("store_detail")
        expense.amount       = old["amount"]
        expense.category     = old["category"]
        expense.notes        = old.get("notes")

    elif log.op_type == "bulk":
        old_cat = payload["old_category"]
        new_cat = payload["new_category"]
        ids     = payload.get("affected_ids", [])
        reverted = 0
        for eid in ids:
            e = Expense.query.get(eid)
            if e and e.category == new_cat:
                e.category = old_cat
                reverted += 1
        flash(f"Undone: {reverted} entries reverted to '{old_cat}'.", "success")

    log.undone = True
    db.session.commit()

    if log.op_type == "edit":
        flash("Undone: entry restored.", "success")

    return redirect(next_url)


@main.route("/expense/<int:expense_id>/delete", methods=["POST"])
def delete_expense(expense_id):
    expense = Expense.query.get_or_404(expense_id)
    db.session.delete(expense)
    db.session.commit()
    flash("Expense deleted.", "success")
    return redirect(request.referrer or url_for("main.expenses"))


@main.route("/expenses/export")
def export_expenses():
    import csv
    import io
    import json as _json

    from flask import Response

    fmt = request.args.get("format", "csv")
    year = request.args.get("year", "")
    month = request.args.get("month", "")
    category = request.args.get("category", "")
    store = request.args.get("store", "")
    search = request.args.get("search", "").strip()

    q = _apply_expense_filters(Expense.query, year, month, category, store, search)
    q = q.order_by(Expense.date.desc().nullslast(), Expense.id.desc())
    expenses = q.all()

    if fmt == "json":
        data = [e.to_dict() for e in expenses]
        return Response(
            _json.dumps(data, ensure_ascii=False, indent=2),
            mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=expenses.json"},
        )

    # CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["date", "store", "store_detail", "amount", "category", "notes"])
    for e in expenses:
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


@main.route("/lang/<code>")
def set_lang(code):
    from app.translations import SUPPORTED_LANGS
    if code in SUPPORTED_LANGS:
        session["lang"] = code
    return redirect(request.referrer or url_for("main.dashboard"))


@main.route("/import", methods=["GET", "POST"])
def import_page():
    result = None
    if request.method == "POST":
        from app.parser import import_csv
        from config import Config
        # "replace" wipes the table and is only honoured with explicit
        # confirmation; the default appends new rows and de-duplicates so
        # manually added or scanned entries are preserved.
        replace = (
            request.form.get("mode") == "replace"
            and request.form.get("confirm_replace") == "yes"
        )
        result = import_csv(Config.CSV_PATH, clear_existing=replace)
        result["mode"] = "replace" if replace else "append"
    return render_template("import.html", result=result)
