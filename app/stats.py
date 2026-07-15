import math
from calendar import monthrange
from datetime import date, timedelta

from flask import g, has_request_context
from sqlalchemy import func

from app import db
from app.models import BudgetPeriod, Expense
from app.parser import CATEGORY_COLORS

# Lowest and highest day-of-month that exists in every month. The period start
# day is clamped to this range so date(year, month, start) can never raise.
_MIN_PERIOD_START_DAY = 1
_MAX_PERIOD_START_DAY = 28
_BUDGET_FALLBACK = 300.0


# ── Period helpers ────────────────────────────────────────────────────────────

def _period_start_day() -> int:
    """Return the configured period start day, clamped to 1–28."""
    try:
        from flask import current_app
        raw = int(current_app.config.get("PERIOD_START_DAY", 1))
    except (RuntimeError, TypeError, ValueError):
        return _MIN_PERIOD_START_DAY
    return max(_MIN_PERIOD_START_DAY, min(_MAX_PERIOD_START_DAY, raw))


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

def _budget_periods() -> list[tuple[date, float]]:
    """All budget rules as (effective_from, monthly_budget), newest first.

    Cached for the duration of a request so repeated budget lookups inside
    aggregation loops (one per month bucket) issue a single query rather than N.
    """
    if has_request_context():
        cached = getattr(g, "_budget_periods_cache", None)
        if cached is not None:
            return cached
    rows = (
        BudgetPeriod.query
        .order_by(BudgetPeriod.effective_from.desc())
        .all()
    )
    periods = [(p.effective_from, p.monthly_budget) for p in rows]
    if has_request_context():
        g._budget_periods_cache = periods
    return periods


def budget_for_month(year: int, month: int) -> float:
    """Return the monthly budget in effect for the given period label."""
    p_start, _ = period_bounds(year, month)
    for effective_from, budget in _budget_periods():
        if effective_from <= p_start:
            return budget
    return _BUDGET_FALLBACK


# ── Basic queries ─────────────────────────────────────────────────────────────

def _dated_expense_rows():
    """All dated expenses as (date, amount, category) rows, cached per request.

    The period-aggregation helpers below must group in Python because the custom
    period label is not expressible in SQL. Sharing one fetch turns the six
    full-table scans a statistics page would otherwise trigger into one.
    """
    if has_request_context():
        cached = getattr(g, "_dated_rows_cache", None)
        if cached is not None:
            return cached
    rows = (
        db.session.query(Expense.date, Expense.amount, Expense.category)
        .filter(Expense.date.isnot(None))
        .all()
    )
    if has_request_context():
        g._dated_rows_cache = rows
    return rows


def get_available_years() -> list[int]:
    """Return sorted list of period-years that have expense data."""
    years = {period_for_date(r.date)[0] for r in _dated_expense_rows()}
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
    rows = _dated_expense_rows()

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


def _trip_interval_stats(
    category: str, lookback_days: int = 120, today: date | None = None
) -> dict | None:
    """
    For a given spending category calculate:
      - average days between separate shopping days
      - average spend per shopping day
      - last trip date + days since last trip
    Returns None if not enough data (<2 trips).

    ``today`` is injectable for tests; defaults to the real current date.
    """
    today = today or date.today()
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
#
# Bayesian end-of-period forecast.
#
# Remaining spend is modelled as a compound sum R = Σ_{i=1..N} X_i:
#
#   * Purchase frequency. The daily purchase rate λ gets a Gamma(α₀, β₀) prior
#     fitted to the last completed periods (empirical Bayes). Prior strength is
#     capped at _FREQ_PRIOR_STRENGTH_DAYS equivalent observation days so the
#     current period's behaviour can shift the posterior. With the purchases
#     observed so far this period the posterior is Gamma(α₀+n, β₀+t), and the
#     posterior predictive for the number of purchases N in the remaining d
#     days is Negative Binomial: E[N] = d·α/β, Var[N] = E[N]·(1 + d/β).
#
#   * Purchase amount. The mean spend per purchase μ gets a conjugate Normal
#     prior centred on the historical mean, with strength capped at
#     _AMOUNT_PRIOR_STRENGTH pseudo-purchases. The per-purchase variance σ² is
#     pooled over history + current period.
#
#   * Compound moments (N independent of the X_i):
#       E[R]   = E[N]·E[μ]
#       Var[R] = E[N]·(σ² + Var[μ]) + Var[N]·E[μ]²
#     summarised with a Normal approximation for the credible interval and the
#     probability of exceeding the budget.
#
# Refunds (negative amounts) are excluded from the purchase model; they still
# reduce the actual spend-so-far via get_monthly_summary.

_PREDICTION_HISTORY_PERIODS = 6     # completed periods used to build the priors
_FREQ_PRIOR_STRENGTH_DAYS = 45.0    # cap on prior weight (in days) for the purchase rate
_AMOUNT_PRIOR_STRENGTH = 25.0       # cap on prior weight (in purchases) for the mean amount
_DEFAULT_AMOUNT_CV = 0.6            # assumed coefficient of variation with <2 observed amounts
_VAGUE_FREQ_SHAPE = 0.5             # Jeffreys-style vague Gamma prior when no history exists
_VAGUE_FREQ_RATE = 1e-6
_CI_Z_90 = 1.6449                   # two-sided 90% normal quantile


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via the error function (no scipy dependency)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _sample_var(xs: list[float]) -> float:
    """Unbiased sample variance (ddof=1). Caller guarantees len(xs) >= 2."""
    m = sum(xs) / len(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def _prediction_inputs(
    year: int, month: int, today: date
) -> tuple[list[tuple[int, int, list[float]]], list[float]]:
    """Collect purchase amounts for the prediction model.

    Args:
        year: Period-year of the current period.
        month: Period-month of the current period.
        today: Reference date; current-period purchases after it are ignored.

    Returns:
        A tuple ``(history, current_amounts)`` where ``history`` holds one
        ``(n_purchases, days_in_period, amounts)`` triple per completed prior
        period that contains at least one positive purchase (periods without
        any purchase are treated as untracked, not as zero-spend), and
        ``current_amounts`` lists the positive amounts of the current period
        up to and including ``today``.
    """
    prior_periods: list[tuple[int, int]] = [
        _prev_month(year, month, n) for n in range(1, _PREDICTION_HISTORY_PERIODS + 1)
    ]
    earliest_start = period_bounds(*prior_periods[-1])[0]

    rows = (
        db.session.query(Expense.date, Expense.amount)
        .filter(
            Expense.date.isnot(None),
            Expense.date >= earliest_start,
            Expense.date <= today,
            Expense.amount > 0,
        )
        .all()
    )

    by_period: dict[tuple[int, int], list[float]] = {}
    for row in rows:
        by_period.setdefault(period_for_date(row.date), []).append(float(row.amount))

    history: list[tuple[int, int, list[float]]] = []
    for py, pm in prior_periods:
        amounts = by_period.get((py, pm))
        if amounts:
            pb_start, pb_end = period_bounds(py, pm)
            history.append((len(amounts), (pb_end - pb_start).days + 1, amounts))

    current_amounts = by_period.get((year, month), [])
    return history, current_amounts


def get_prediction(year: int, month: int, today: date | None = None) -> dict:
    """Bayesian end-of-period forecast for the current budget period.

    Combines a Gamma-Poisson model of purchase frequency with a conjugate
    Normal model of spend per purchase (see module comment above) into a
    posterior predictive distribution of the end-of-period total. Reports the
    posterior mean, a 90% credible interval, and the probability of exceeding
    the budget. Trip-interval details for groceries and fuel are included for
    display purposes only.

    Args:
        year: Period-year to predict for.
        month: Period-month to predict for.
        today: Injectable reference date for tests; defaults to date.today().

    Returns:
        A dict with ``has_prediction`` False for past/future periods, otherwise
        the forecast fields consumed by the dashboard and the JSON API.
    """
    today = today or date.today()
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
    budget = current["budget"]

    history, cur_amounts = _prediction_inputs(year, month, today)
    hist_amounts = [a for _, _, amounts in history for a in amounts]
    n_hist = sum(n for n, _, _ in history)
    d_hist = sum(d for _, d, _ in history)
    n_cur = len(cur_amounts)

    # ── Purchase frequency: Gamma prior → Gamma posterior on λ (purchases/day)
    if n_hist > 0 and d_hist > 0:
        kappa_f = min(float(d_hist), _FREQ_PRIOR_STRENGTH_DAYS)
        alpha0 = (n_hist / d_hist) * kappa_f
        beta0 = kappa_f
    else:
        alpha0, beta0 = _VAGUE_FREQ_SHAPE, _VAGUE_FREQ_RATE
    alpha = alpha0 + n_cur
    beta = beta0 + days_elapsed

    exp_n = days_remaining * alpha / beta                 # E[N] (Negative Binomial)
    var_n = exp_n * (1.0 + days_remaining / beta)         # Var[N]

    # ── Spend per purchase: conjugate Normal posterior on μ
    pooled = hist_amounts + cur_amounts
    if pooled:
        m0 = (
            sum(hist_amounts) / len(hist_amounts)
            if hist_amounts
            else sum(cur_amounts) / n_cur
        )
        sigma2 = _sample_var(pooled) if len(pooled) >= 2 else (m0 * _DEFAULT_AMOUNT_CV) ** 2
        kappa_a = min(float(len(hist_amounts)), _AMOUNT_PRIOR_STRENGTH)
        kappa_n = kappa_a + n_cur
        m_post = (kappa_a * m0 + sum(cur_amounts)) / kappa_n
        var_mu = sigma2 / kappa_n

        exp_r = exp_n * m_post
        var_r = exp_n * (sigma2 + var_mu) + var_n * m_post**2
    else:
        # No purchases anywhere: nothing to learn from, forecast flat.
        m_post = 0.0
        exp_r = 0.0
        var_r = 0.0

    sd_r = math.sqrt(var_r)
    predicted_total = round(current["total"] + exp_r, 2)
    predicted_remaining = round(budget - predicted_total, 2)
    ci_low_total = round(current["total"] + max(0.0, exp_r - _CI_Z_90 * sd_r), 2)
    ci_high_total = round(current["total"] + exp_r + _CI_Z_90 * sd_r, 2)

    headroom = budget - current["total"] - exp_r
    if sd_r > 0:
        prob_over = 1.0 - _norm_cdf(headroom / sd_r)
    else:
        prob_over = 1.0 if headroom < 0 else 0.0
    prob_over_pct = round(prob_over * 100, 1)

    # Posterior expected daily spend going forward (for the dashboard card).
    avg_daily_rate = round((alpha / beta) * m_post, 2)

    # ── Trip-based details (groceries & fuel) — display only ────────────────
    grocery_stats = _trip_interval_stats("Lebensmittel", today=today)
    fuel_stats = _trip_interval_stats("Tanken", today=today)

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

    return {
        "has_prediction": True,
        "method": "bayesian",
        "days_elapsed": days_elapsed,
        "days_remaining": days_remaining,
        "days_in_month": days_in_period,
        "avg_daily_rate": avg_daily_rate,
        # Posterior forecast
        "predicted_total": predicted_total,
        "predicted_remaining": predicted_remaining,
        "ci_level": 90,
        "ci_low_total": ci_low_total,
        "ci_high_total": ci_high_total,
        "prob_over_budget": prob_over_pct,
        "will_exceed_budget": prob_over >= 0.5,
        "expected_purchases_remaining": round(exp_n, 1),
        "posterior_mean_per_purchase": round(m_post, 2),
        # Trip details (display only)
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
    rows = _dated_expense_rows()

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
    rows = _dated_expense_rows()

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
    rows = _dated_expense_rows()

    buckets: dict[tuple[int, int], float] = {}
    for row in rows:
        key = period_for_date(row.date)
        buckets[key] = buckets.get(key, 0.0) + row.amount

    results: list[dict] = []
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

    rows = _dated_expense_rows()

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
