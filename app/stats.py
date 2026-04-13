from datetime import date, timedelta
from calendar import monthrange
from sqlalchemy import func, extract
from app import db
from app.models import Expense, BudgetPeriod
from app.parser import CATEGORY_COLORS, ALL_CATEGORIES


# ── Period helpers ────────────────────────────────────────────────────────────

def _period_start_day() -> int:
    """Return the configured period start day (1–28)."""
    try:
        from flask import current_app
        return int(current_app.config.get("PERIOD_START_DAY", 1))
    except RuntimeError:
        return 1


def period_for_date(d: date) -> tuple[int, int]:
    """Map an expense date to its budget period label (year, month).

    With start_day=7: April 2 → (year, 3) [March period],
                      April 7 → (year, 4) [April period].
    With start_day=1: behaves like calendar months.
    """
    start = _period_start_day()
    if start <= 1 or d.day >= start:
        return d.year, d.month
    if d.month == 1:
        return d.year - 1, 12
    return d.year, d.month - 1


def period_bounds(year: int, month: int) -> tuple[date, date]:
    """Return the [start, end] dates (inclusive) for a budget period label."""
    start = _period_start_day()
    if start <= 1:
        last_day = monthrange(year, month)[1]
        return date(year, month, 1), date(year, month, last_day)
    p_start = date(year, month, start)
    if month == 12:
        p_end = date(year + 1, 1, start) - timedelta(days=1)
    else:
        p_end = date(year, month + 1, start) - timedelta(days=1)
    return p_start, p_end


def _year_bounds(year: int) -> tuple[date, date]:
    """Return the date range that covers all periods labelled with this year."""
    start = _period_start_day()
    if start <= 1:
        return date(year, 1, 1), date(year, 12, 31)
    return date(year, 1, start), date(year + 1, 1, start) - timedelta(days=1)


# ── Budget lookup ─────────────────────────────────────────────────────────────

def budget_for_month(year: int, month: int) -> float:
    """Return the monthly budget in effect for the given period label."""
    p_start, _ = period_bounds(year, month)
    period = (
        BudgetPeriod.query
        .filter(BudgetPeriod.effective_from <= p_start)
        .order_by(BudgetPeriod.effective_from.desc())
        .first()
    )
    return period.monthly_budget if period else 300.0


# ── Basic queries ─────────────────────────────────────────────────────────────

def get_available_years() -> list[int]:
    """Return sorted list of period-years that have expense data."""
    rows = (
        db.session.query(Expense.date)
        .filter(Expense.date.isnot(None))
        .all()
    )
    years = {period_for_date(r.date)[0] for r in rows}
    return sorted(years)


def get_monthly_summary(year: int, month: int) -> dict:
    """Total spending, budget, and remaining for one period."""
    p_start, p_end = period_bounds(year, month)
    total = (
        db.session.query(func.sum(Expense.amount))
        .filter(
            Expense.date.isnot(None),
            Expense.date >= p_start,
            Expense.date <= p_end,
        )
        .scalar()
        or 0.0
    )
    budget = budget_for_month(year, month)
    return {
        "year": year,
        "month": month,
        "total": round(total, 2),
        "budget": budget,
        "remaining": round(budget - total, 2),
        "percent_used": round(min((total / budget) * 100, 100), 1) if budget else 0,
    }


def get_monthly_transaction_count(year: int, month: int) -> int:
    p_start, p_end = period_bounds(year, month)
    return (
        db.session.query(func.count(Expense.id))
        .filter(
            Expense.date.isnot(None),
            Expense.date >= p_start,
            Expense.date <= p_end,
        )
        .scalar()
        or 0
    )


def get_monthly_trends(years: list[int] | None = None) -> list[dict]:
    """Monthly spending totals grouped by period, across all (or selected) years."""
    rows = (
        db.session.query(Expense.date, Expense.amount)
        .filter(Expense.date.isnot(None))
        .all()
    )

    buckets: dict[tuple[int, int], dict] = {}
    for row in rows:
        py, pm = period_for_date(row.date)
        if years and py not in years:
            continue
        key = (py, pm)
        if key not in buckets:
            buckets[key] = {"total": 0.0, "count": 0}
        buckets[key]["total"] += row.amount
        buckets[key]["count"] += 1

    results = []
    for (y, m), data in sorted(buckets.items()):
        total = round(data["total"], 2)
        bdg = budget_for_month(y, m)
        results.append(
            {
                "year": y,
                "month": m,
                "label": f"{m:02d}/{y}",
                "total": total,
                "budget": bdg,
                "over_budget": total > bdg,
                "count": data["count"],
            }
        )
    return results


def get_category_breakdown(year: int | None = None, month: int | None = None) -> list[dict]:
    """Spending totals grouped by category, sorted descending. Includes monthly limit info."""
    q = db.session.query(
        Expense.category,
        func.sum(Expense.amount).label("total"),
        func.count(Expense.id).label("count"),
    )
    if year and month:
        p_start, p_end = period_bounds(year, month)
        q = q.filter(Expense.date >= p_start, Expense.date <= p_end)
    elif year:
        y_start, y_end = _year_bounds(year)
        q = q.filter(Expense.date >= y_start, Expense.date <= y_end)
    q = q.group_by(Expense.category).order_by(func.sum(Expense.amount).desc())

    limits = get_category_limits() if (year and month) else {}
    results = []
    for row in q.all():
        cat = row.category or "Sonstiges"
        total = round(float(row.total), 2)
        limit = limits.get(cat)
        results.append({
            "category": cat,
            "total": total,
            "count": int(row.count),
            "color": CATEGORY_COLORS.get(cat, "#9E9E9E"),
            "limit": limit,
            "over_limit": (total > limit) if limit is not None else False,
            "limit_pct": round(min(total / limit * 100, 100), 1) if limit else None,
        })
    return results


def get_store_breakdown(
    year: int | None = None, month: int | None = None, limit: int = 10
) -> list[dict]:
    """Top stores by total spending. Store names are resolved through alias map."""
    q = db.session.query(
        Expense.store,
        Expense.store_detail,
        func.sum(Expense.amount).label("total"),
        func.count(Expense.id).label("count"),
    )
    if year and month:
        p_start, p_end = period_bounds(year, month)
        q = q.filter(Expense.date >= p_start, Expense.date <= p_end)
    elif year:
        y_start, y_end = _year_bounds(year)
        q = q.filter(Expense.date >= y_start, Expense.date <= y_end)
    q = q.group_by(Expense.store, Expense.store_detail)

    alias_map = get_alias_map()
    merged: dict[str, dict] = {}
    for row in q.all():
        raw = row.store_detail if row.store_detail else row.store
        canonical = alias_map.get(raw, raw)
        if canonical not in merged:
            merged[canonical] = {"total": 0.0, "count": 0}
        merged[canonical]["total"] += float(row.total)
        merged[canonical]["count"] += int(row.count)

    sorted_stores = sorted(merged.items(), key=lambda x: x[1]["total"], reverse=True)[:limit]
    return [
        {"store": name, "display_store": name, "total": round(d["total"], 2), "count": d["count"]}
        for name, d in sorted_stores
    ]


def get_distinct_stores() -> list[str]:
    """All unique canonical store names for filter dropdowns (aliases applied)."""
    rows = db.session.query(Expense.store, Expense.store_detail).all()
    alias_map = get_alias_map()
    names: set[str] = set()
    for row in rows:
        raw = row.store_detail if row.store_detail else row.store
        names.add(alias_map.get(raw, raw))
    return sorted(names)


# ── Alias & limit helpers ─────────────────────────────────────────────────────

def get_alias_map() -> dict[str, str]:
    """Return {raw_name: canonical_name} for all defined store aliases."""
    from app.models import StoreAlias
    return {sa.alias: sa.canonical for sa in StoreAlias.query.all()}


def get_category_limits() -> dict[str, float]:
    """Return {category: monthly_limit} for all defined category budgets."""
    from app.models import CategoryBudget
    return {cb.category: cb.monthly_limit for cb in CategoryBudget.query.all()}


def get_store_anomaly_thresholds(min_samples: int = 5) -> dict[str, tuple[float, float]]:
    """
    Return {canonical_store: (mean, std)} for stores with >= min_samples positive-amount entries.
    Only positive amounts are used so refunds don't distort the average.
    """
    rows = (
        db.session.query(Expense.store, Expense.store_detail, Expense.amount)
        .filter(Expense.date.isnot(None), Expense.amount > 0)
        .all()
    )
    alias_map = get_alias_map()
    buckets: dict[str, list[float]] = {}
    for row in rows:
        raw = row.store_detail if row.store_detail else row.store
        canonical = alias_map.get(raw, raw)
        buckets.setdefault(canonical, []).append(row.amount)

    result: dict[str, tuple[float, float]] = {}
    for store, amounts in buckets.items():
        if len(amounts) >= min_samples:
            mean = sum(amounts) / len(amounts)
            std = (sum((x - mean) ** 2 for x in amounts) / len(amounts)) ** 0.5
            result[store] = (mean, std)
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _prev_month(year: int, month: int, n: int = 1):
    """Return (year, month) n period-months before the given period label."""
    month -= n
    while month <= 0:
        month += 12
        year -= 1
    return year, month


def _trip_interval_stats(category: str, lookback_days: int = 120) -> dict | None:
    """
    For a given spending category calculate:
      - average days between separate shopping days
      - average spend per shopping day
      - last trip date + days since last trip
    Returns None if not enough data (<2 trips).
    """
    today = date.today()
    cutoff = today - timedelta(days=lookback_days)

    rows = (
        db.session.query(
            Expense.date,
            func.sum(Expense.amount).label("total"),
        )
        .filter(
            Expense.category == category,
            Expense.date.isnot(None),
            Expense.date >= cutoff,
            Expense.date < today,
        )
        .group_by(Expense.date)
        .order_by(Expense.date)
        .all()
    )

    if len(rows) < 2:
        return None

    dates = [r.date for r in rows]
    totals = [float(r.total) for r in rows]
    gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]

    return {
        "avg_interval_days": round(sum(gaps) / len(gaps), 1),
        "avg_spend_per_trip": round(sum(totals) / len(totals), 2),
        "last_trip_date": dates[-1].isoformat(),
        "days_since_last": (today - dates[-1]).days,
        "sample_size": len(dates),
    }


# ── Prediction ────────────────────────────────────────────────────────────────

def get_prediction(year: int, month: int) -> dict:
    """
    Predict end-of-period budget remaining using two approaches:

    1. Rate-based: avg daily spend from last 3 completed periods × days left.
    2. Trip-based: expected remaining grocery & fuel trips × avg cost per trip.

    Only meaningful for the current period.
    """
    today = date.today()
    cur_year, cur_month = period_for_date(today)

    if not (year == cur_year and month == cur_month):
        return {"has_prediction": False}

    p_start, p_end = period_bounds(year, month)
    days_in_period = (p_end - p_start).days + 1
    days_elapsed = (today - p_start).days + 1
    days_remaining = (p_end - today).days

    if days_remaining < 0:
        return {"has_prediction": False}

    current = get_monthly_summary(year, month)

    # --- Rate-based projection ---
    historical_rates = []
    for n in range(1, 4):
        py, pm = _prev_month(year, month, n)
        ps = get_monthly_summary(py, pm)
        pb_start, pb_end = period_bounds(py, pm)
        pd_count = (pb_end - pb_start).days + 1
        if ps["total"] > 0:
            historical_rates.append(ps["total"] / pd_count)

    if historical_rates:
        avg_daily_rate = sum(historical_rates) / len(historical_rates)
    elif days_elapsed > 0:
        avg_daily_rate = current["total"] / days_elapsed
    else:
        avg_daily_rate = 0.0

    rate_additional = round(avg_daily_rate * days_remaining, 2)
    rate_predicted_total = round(current["total"] + rate_additional, 2)
    rate_predicted_remaining = round(current["budget"] - rate_predicted_total, 2)

    # --- Trip-based projection ---
    grocery_stats = _trip_interval_stats("Lebensmittel")
    fuel_stats = _trip_interval_stats("Tanken")

    def _expected_remaining_trips(trip_stats: dict | None) -> int | None:
        if not trip_stats:
            return None
        interval = trip_stats["avg_interval_days"]
        last_date = date.fromisoformat(trip_stats["last_trip_date"])
        count = 0
        next_trip = last_date + timedelta(days=max(1, round(interval)))
        while next_trip <= p_end:
            if next_trip > today:
                count += 1
            next_trip += timedelta(days=max(1, round(interval)))
        return count

    grocery_remaining = _expected_remaining_trips(grocery_stats)
    fuel_remaining = _expected_remaining_trips(fuel_stats)

    trip_additional = 0.0
    if grocery_stats and grocery_remaining is not None:
        trip_additional += grocery_remaining * grocery_stats["avg_spend_per_trip"]
    if fuel_stats and fuel_remaining is not None:
        trip_additional += fuel_remaining * fuel_stats["avg_spend_per_trip"]

    trip_predicted_total = round(current["total"] + trip_additional, 2)
    trip_predicted_remaining = round(current["budget"] - trip_predicted_total, 2)

    if grocery_stats or fuel_stats:
        blended_total = round((rate_predicted_total + trip_predicted_total) / 2, 2)
    else:
        blended_total = rate_predicted_total
    blended_remaining = round(current["budget"] - blended_total, 2)

    return {
        "has_prediction": True,
        "days_elapsed": days_elapsed,
        "days_remaining": days_remaining,
        "days_in_month": days_in_period,
        "avg_daily_rate": round(avg_daily_rate, 2),
        # Rate-based
        "rate_additional": rate_additional,
        "rate_predicted_total": rate_predicted_total,
        "rate_predicted_remaining": rate_predicted_remaining,
        # Trip-based
        "trip_additional": round(trip_additional, 2),
        "trip_predicted_total": trip_predicted_total,
        "trip_predicted_remaining": trip_predicted_remaining,
        # Blended
        "predicted_total": blended_total,
        "predicted_remaining": blended_remaining,
        "will_exceed_budget": blended_total > current["budget"],
        # Trip details
        "grocery": {
            **grocery_stats,
            "expected_remaining_trips": grocery_remaining,
            "projected_spend": round(grocery_remaining * grocery_stats["avg_spend_per_trip"], 2)
            if grocery_remaining is not None else None,
        } if grocery_stats else None,
        "fuel": {
            **fuel_stats,
            "expected_remaining_trips": fuel_remaining,
            "projected_spend": round(fuel_remaining * fuel_stats["avg_spend_per_trip"], 2)
            if fuel_remaining is not None else None,
        } if fuel_stats else None,
    }


# ── Advanced Statistics ───────────────────────────────────────────────────────

def get_yearly_comparison() -> dict:
    """
    Monthly totals per period-year — for a grouped bar chart.
    Returns { years: [2022, 2023, ...], data: {year: [jan_total, ...]} }
    """
    rows = (
        db.session.query(Expense.date, Expense.amount)
        .filter(Expense.date.isnot(None))
        .all()
    )

    lookup: dict[int, dict[int, float]] = {}
    for row in rows:
        py, pm = period_for_date(row.date)
        if py not in lookup:
            lookup[py] = {}
        lookup[py][pm] = lookup[py].get(pm, 0.0) + row.amount

    years = sorted(lookup.keys())
    data = {y: [round(lookup[y].get(m, 0.0), 2) for m in range(1, 13)] for y in years}
    return {"years": years, "data": data}


def get_category_trends() -> list[dict]:
    """
    Category totals per period for a stacked bar / area chart.
    Returns [{label, year, month, <category>: total, ...}, ...]
    """
    rows = (
        db.session.query(Expense.date, Expense.category, Expense.amount)
        .filter(Expense.date.isnot(None))
        .all()
    )

    points: dict[tuple[int, int], dict] = {}
    for row in rows:
        key = period_for_date(row.date)
        if key not in points:
            points[key] = {}
        cat = row.category or "Sonstiges"
        points[key][cat] = points[key].get(cat, 0.0) + row.amount

    result = []
    for (y, m), cats in sorted(points.items()):
        entry = {"label": f"{m:02d}/{y}", "year": y, "month": m}
        entry.update({k: round(v, 2) for k, v in cats.items()})
        result.append(entry)
    return result


def get_top_months(limit: int = 5, best: bool = False) -> list[dict]:
    """
    Top periods by budget surplus (best=True) or overspend (best=False).
    """
    rows = (
        db.session.query(Expense.date, Expense.amount)
        .filter(Expense.date.isnot(None))
        .all()
    )

    buckets: dict[tuple[int, int], float] = {}
    for row in rows:
        key = period_for_date(row.date)
        buckets[key] = buckets.get(key, 0.0) + row.amount

    results = []
    for (y, m), total in buckets.items():
        total = round(total, 2)
        budget = budget_for_month(y, m)
        surplus = round(budget - total, 2)
        results.append({
            "year": y, "month": m,
            "label": f"{m:02d}/{y}",
            "total": total, "budget": budget,
            "surplus": surplus,
            "over_budget": surplus < 0,
        })

    results.sort(key=lambda x: x["surplus"], reverse=best)
    return results[:limit]


def get_overall_stats() -> dict:
    """Global headline numbers across all data."""
    total_spend = db.session.query(func.sum(Expense.amount)).scalar() or 0.0
    total_count = db.session.query(func.count(Expense.id)).scalar() or 0

    rows = (
        db.session.query(Expense.date, Expense.amount)
        .filter(Expense.date.isnot(None))
        .all()
    )

    buckets: dict[tuple[int, int], float] = {}
    for row in rows:
        key = period_for_date(row.date)
        buckets[key] = buckets.get(key, 0.0) + row.amount

    months_tracked = len(buckets)
    monthly_totals = list(buckets.values())
    avg_monthly = round(sum(monthly_totals) / months_tracked, 2) if months_tracked else 0.0
    max_monthly = round(max(monthly_totals), 2) if monthly_totals else 0.0
    min_monthly = round(min(monthly_totals), 2) if monthly_totals else 0.0

    avg_per_trip = round(float(total_spend) / total_count, 2) if total_count else 0.0

    over_budget_count = sum(
        1 for (y, m), total in buckets.items()
        if total > budget_for_month(y, m)
    )

    return {
        "total_spend": round(float(total_spend), 2),
        "total_count": total_count,
        "months_tracked": months_tracked,
        "avg_monthly": avg_monthly,
        "max_monthly": max_monthly,
        "min_monthly": min_monthly,
        "avg_per_trip": avg_per_trip,
        "over_budget_count": over_budget_count,
        "on_budget_pct": round((1 - over_budget_count / months_tracked) * 100, 1) if months_tracked else 0,
    }
