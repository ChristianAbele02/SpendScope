"""Analytics: budget periods, aggregations, forecast and anomaly detection.

All "month" arguments are *budget period* labels. With PERIOD_START_DAY = 7
the period labelled (2024, 3) runs from 7 March to 6 April; see period_bounds.
"""
import math
from calendar import monthrange
from collections.abc import Callable
from datetime import date, timedelta
from typing import TypeVar

from flask import current_app, g, has_request_context
from sqlalchemy import func

from app import db
from app.models import BudgetPeriod, CategoryBudget, Expense, StoreAlias
from app.parser import CATEGORY_COLORS, DEFAULT_CATEGORY

# Lowest and highest day-of-month that exists in every month. The period start
# day is clamped to this range so date(year, month, start) can never raise.
_MIN_PERIOD_START_DAY = 1
_MAX_PERIOD_START_DAY = 28
_BUDGET_FALLBACK = 300.0
_FALLBACK_COLOR = CATEGORY_COLORS[DEFAULT_CATEGORY]

# An expense is flagged as unusual when it exceeds mean + k·SD for its store.
_ANOMALY_SIGMA = 2.0
_ANOMALY_MIN_SAMPLES = 5

T = TypeVar("T")


def _request_cached(key: str, loader: Callable[[], T]) -> T:
    """Return ``loader()``, memoised on ``flask.g`` for the current request.

    Outside a request (CLI, tests using only an app context) nothing is cached.
    """
    if has_request_context():
        cached = g.get(key)
        if cached is not None:
            return cached
    value = loader()
    if has_request_context():
        setattr(g, key, value)
    return value


# ── Period helpers ────────────────────────────────────────────────────────────

def _period_start_day() -> int:
    """Return the configured period start day, clamped to 1–28."""
    try:
        raw = int(current_app.config.get("PERIOD_START_DAY", 1))
    except (RuntimeError, TypeError, ValueError):
        return _MIN_PERIOD_START_DAY
    return max(_MIN_PERIOD_START_DAY, min(_MAX_PERIOD_START_DAY, raw))


def period_for_date(d: date) -> tuple[int, int]:
    """Map an expense date to its budget period label (year, month).

    With start day 7: 2 April → (year, 3) [March period],
                      7 April → (year, 4) [April period].
    With start day 1 this is the calendar month.
    """
    start = _period_start_day()
    if start <= 1 or d.day >= start:
        return d.year, d.month
    if d.month == 1:
        return d.year - 1, 12
    return d.year, d.month - 1


def period_bounds(year: int, month: int) -> tuple[date, date]:
    """Return the inclusive [start, end] dates of a budget period label."""
    start = _period_start_day()
    if start <= 1:
        return date(year, month, 1), date(year, month, monthrange(year, month)[1])
    p_start = date(year, month, start)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return p_start, date(next_year, next_month, start) - timedelta(days=1)


def year_bounds(year: int) -> tuple[date, date]:
    """Return the inclusive date range covering all twelve periods of a year label."""
    start = _period_start_day()
    if start <= 1:
        return date(year, 1, 1), date(year, 12, 31)
    return date(year, 1, start), date(year + 1, 1, start) - timedelta(days=1)


def _prev_month(year: int, month: int, n: int = 1) -> tuple[int, int]:
    """Return the period label ``n`` periods before (year, month)."""
    month -= n
    while month <= 0:
        month += 12
        year -= 1
    return year, month


# ── Budget lookup ─────────────────────────────────────────────────────────────

def _budget_periods() -> list[tuple[date, float]]:
    """All budget rules as (effective_from, monthly_budget), newest first.

    Cached per request so aggregation loops that look up one budget per period
    issue a single query instead of one per period.
    """
    def load() -> list[tuple[date, float]]:
        rows = BudgetPeriod.query.order_by(BudgetPeriod.effective_from.desc()).all()
        return [(p.effective_from, p.monthly_budget) for p in rows]

    return _request_cached("_budget_periods_cache", load)


def budget_for_month(year: int, month: int) -> float:
    """Return the budget of the newest rule effective on or before the period start."""
    p_start, _ = period_bounds(year, month)
    for effective_from, budget in _budget_periods():
        if effective_from <= p_start:
            return budget
    return _BUDGET_FALLBACK


# ── Shared lookups ────────────────────────────────────────────────────────────

def _dated_expense_rows() -> list:
    """All dated expenses as (date, amount, category) rows, cached per request.

    Period labels cannot be expressed in SQL, so period aggregations group in
    Python. Sharing one fetch avoids a full table scan per chart.
    """
    return _request_cached(
        "_dated_rows_cache",
        lambda: (
            db.session.query(Expense.date, Expense.amount, Expense.category)
            .filter(Expense.date.isnot(None))
            .all()
        ),
    )


def _period_totals() -> dict[tuple[int, int], float]:
    """Total spend per period label across all dated expenses."""
    totals: dict[tuple[int, int], float] = {}
    for row in _dated_expense_rows():
        key = period_for_date(row.date)
        totals[key] = totals.get(key, 0.0) + row.amount
    return totals


def get_alias_map() -> dict[str, str]:
    """Return {raw_name: canonical_name} for all store aliases (cached per request)."""
    return _request_cached(
        "_alias_map_cache",
        lambda: {sa.alias: sa.canonical for sa in StoreAlias.query.all()},
    )


def _canonical_store(store: str, detail: str | None, alias_map: dict[str, str]) -> str:
    """Resolve a raw (store, store_detail) pair to the name shown in the UI."""
    raw = detail or store
    return alias_map.get(raw, raw)


def get_category_limits() -> dict[str, float]:
    """Return {category: monthly_limit} for all category budgets."""
    return {cb.category: cb.monthly_limit for cb in CategoryBudget.query.all()}


def get_available_years() -> list[int]:
    """Return the sorted period-years that contain expense data."""
    return sorted({period_for_date(r.date)[0] for r in _dated_expense_rows()})


def get_distinct_stores() -> list[str]:
    """All canonical store names (aliases applied), sorted, for filter dropdowns."""
    alias_map = get_alias_map()
    rows = db.session.query(Expense.store, Expense.store_detail).distinct().all()
    return sorted({_canonical_store(r.store, r.store_detail, alias_map) for r in rows})


# ── Period summaries ──────────────────────────────────────────────────────────

def _filter_period(q, year: int | None, month: int | None):
    """Restrict a query to one period (year + month), a whole year, or nothing."""
    if year and month:
        start, end = period_bounds(year, month)
    elif year:
        start, end = year_bounds(year)
    else:
        return q
    return q.filter(Expense.date >= start, Expense.date <= end)


def get_monthly_summary(year: int, month: int) -> dict:
    """Total spend, budget, remaining budget and percentage used for one period."""
    total = _filter_period(db.session.query(func.sum(Expense.amount)), year, month).scalar() or 0.0
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
    """Number of expenses (refunds included) in one period."""
    return _filter_period(db.session.query(func.count(Expense.id)), year, month).scalar() or 0


def get_monthly_trends(years: list[int] | None = None) -> list[dict]:
    """Per-period totals and budgets, optionally restricted to some period-years."""
    buckets: dict[tuple[int, int], dict] = {}
    for row in _dated_expense_rows():
        key = period_for_date(row.date)
        if years and key[0] not in years:
            continue
        bucket = buckets.setdefault(key, {"total": 0.0, "count": 0})
        bucket["total"] += row.amount
        bucket["count"] += 1

    results = []
    for (y, m), data in sorted(buckets.items()):
        total = round(data["total"], 2)
        budget = budget_for_month(y, m)
        results.append({
            "year": y,
            "month": m,
            "label": f"{m:02d}/{y}",
            "total": total,
            "budget": budget,
            "over_budget": total > budget,
            "count": data["count"],
        })
    return results


def get_category_breakdown(year: int | None = None, month: int | None = None) -> list[dict]:
    """Spend per category, largest first. Limit fields are filled for single periods."""
    q = db.session.query(
        Expense.category,
        func.sum(Expense.amount).label("total"),
        func.count(Expense.id).label("count"),
    )
    q = _filter_period(q, year, month)
    q = q.group_by(Expense.category).order_by(func.sum(Expense.amount).desc())

    limits = get_category_limits() if (year and month) else {}
    results = []
    for row in q.all():
        cat = row.category or DEFAULT_CATEGORY
        total = round(float(row.total), 2)
        limit = limits.get(cat)
        results.append({
            "category": cat,
            "total": total,
            "count": int(row.count),
            "color": CATEGORY_COLORS.get(cat, _FALLBACK_COLOR),
            "limit": limit,
            "over_limit": (total > limit) if limit is not None else False,
            "limit_pct": round(min(total / limit * 100, 100), 1) if limit else None,
        })
    return results


def get_store_breakdown(
    year: int | None = None, month: int | None = None, limit: int = 10
) -> list[dict]:
    """Top stores by total spend, with aliases merged into their canonical name."""
    q = db.session.query(
        Expense.store,
        Expense.store_detail,
        func.sum(Expense.amount).label("total"),
        func.count(Expense.id).label("count"),
    )
    q = _filter_period(q, year, month).group_by(Expense.store, Expense.store_detail)

    alias_map = get_alias_map()
    merged: dict[str, dict] = {}
    for row in q.all():
        entry = merged.setdefault(
            _canonical_store(row.store, row.store_detail, alias_map), {"total": 0.0, "count": 0}
        )
        entry["total"] += float(row.total)
        entry["count"] += int(row.count)

    top = sorted(merged.items(), key=lambda kv: kv[1]["total"], reverse=True)[:limit]
    return [
        {"store": name, "display_store": name, "total": round(d["total"], 2), "count": d["count"]}
        for name, d in top
    ]


def get_store_anomaly_thresholds(
    min_samples: int = _ANOMALY_MIN_SAMPLES,
) -> dict[str, tuple[float, float]]:
    """Per-store thresholds above which an expense is flagged as unusually high.

    Only positive amounts are used, so refunds do not distort the statistics.
    Stores with fewer than ``min_samples`` purchases, or with no variation at
    all, are omitted.

    Returns:
        ``{canonical_store: (mean, threshold)}`` with
        ``threshold = mean + _ANOMALY_SIGMA · SD`` (population SD).
    """
    rows = (
        db.session.query(Expense.store, Expense.store_detail, Expense.amount)
        .filter(Expense.date.isnot(None), Expense.amount > 0)
        .all()
    )
    alias_map = get_alias_map()
    buckets: dict[str, list[float]] = {}
    for row in rows:
        buckets.setdefault(_canonical_store(row.store, row.store_detail, alias_map), []).append(
            row.amount
        )

    result: dict[str, tuple[float, float]] = {}
    for store, amounts in buckets.items():
        if len(amounts) < min_samples:
            continue
        mean = sum(amounts) / len(amounts)
        sd = math.sqrt(sum((x - mean) ** 2 for x in amounts) / len(amounts))
        if sd > 0:
            result[store] = (mean, mean + _ANOMALY_SIGMA * sd)
    return result


# ── Shopping intervals ────────────────────────────────────────────────────────

def trip_interval_stats(
    category: str, lookback_days: int = 120, today: date | None = None
) -> dict | None:
    """Shopping rhythm for one category over the last ``lookback_days``.

    Purchases on the same day count as one trip.

    Args:
        category: Category to analyse, e.g. ``"Lebensmittel"``.
        lookback_days: Size of the window ending yesterday.
        today: Injectable reference date for tests.

    Returns:
        Average days between trips, average spend per trip, last trip date,
        days since the last trip and the number of trips, or ``None`` when
        the window contains fewer than two trips.
    """
    today = today or date.today()
    cutoff = today - timedelta(days=lookback_days)

    rows = (
        db.session.query(Expense.date, func.sum(Expense.amount).label("total"))
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
    gaps = [(later - earlier).days for earlier, later in zip(dates, dates[1:], strict=False)]

    return {
        "avg_interval_days": round(sum(gaps) / len(gaps), 1),
        "avg_spend_per_trip": round(sum(totals) / len(totals), 2),
        "last_trip_date": dates[-1].isoformat(),
        "days_since_last": (today - dates[-1]).days,
        "sample_size": len(dates),
    }


def _with_trip_projection(trip_stats: dict | None, today: date, period_end: date) -> dict | None:
    """Add the expected number of remaining trips and their spend until ``period_end``.

    Trips are extrapolated from the last trip at the average interval.
    """
    if not trip_stats:
        return None
    step = timedelta(days=max(1, round(trip_stats["avg_interval_days"])))
    next_trip = date.fromisoformat(trip_stats["last_trip_date"]) + step
    remaining = 0
    while next_trip <= period_end:
        if next_trip > today:
            remaining += 1
        next_trip += step
    return {
        **trip_stats,
        "expected_remaining_trips": remaining,
        "projected_spend": round(remaining * trip_stats["avg_spend_per_trip"], 2),
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
# reduce the actual spend so far via get_monthly_summary.

_PREDICTION_HISTORY_PERIODS = 6     # completed periods used to build the priors
_FREQ_PRIOR_STRENGTH_DAYS = 45.0    # cap on prior weight (in days) for the purchase rate
_AMOUNT_PRIOR_STRENGTH = 25.0       # cap on prior weight (in purchases) for the mean amount
_DEFAULT_AMOUNT_CV = 0.6            # assumed coefficient of variation with <2 observed amounts
_VAGUE_FREQ_SHAPE = 0.5             # Jeffreys-style vague Gamma prior when no history exists
_VAGUE_FREQ_RATE = 1e-6
_CI_LEVEL = 90
_CI_Z_90 = 1.6449                   # two-sided 90% normal quantile
_TRIP_LOOKBACK_DAYS = 120


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
    prior_periods = [
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
    for label in prior_periods:
        amounts = by_period.get(label)
        if amounts:
            pb_start, pb_end = period_bounds(*label)
            history.append((len(amounts), (pb_end - pb_start).days + 1, amounts))

    return history, by_period.get((year, month), [])


def get_prediction(year: int, month: int, today: date | None = None) -> dict:
    """Bayesian end-of-period forecast for the current budget period.

    Combines a Gamma-Poisson model of purchase frequency with a conjugate
    Normal model of spend per purchase (see the module comment above) into a
    posterior predictive distribution of the end-of-period total. Reports the
    posterior mean, a 90% credible interval, and the probability of exceeding
    the budget. Trip-interval details for groceries and fuel are included for
    display only and do not enter the forecast.

    Args:
        year: Period-year to predict for.
        month: Period-month to predict for.
        today: Injectable reference date for tests; defaults to date.today().

    Returns:
        ``{"has_prediction": False}`` for any period other than the current
        one, otherwise the forecast fields consumed by the dashboard and API.
    """
    today = today or date.today()
    if (year, month) != period_for_date(today):
        return {"has_prediction": False}

    p_start, p_end = period_bounds(year, month)
    days_in_period = (p_end - p_start).days + 1
    days_elapsed = (today - p_start).days + 1
    days_remaining = (p_end - today).days

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
        m0 = sum(hist_amounts) / len(hist_amounts) if hist_amounts else sum(cur_amounts) / n_cur
        sigma2 = _sample_var(pooled) if len(pooled) >= 2 else (m0 * _DEFAULT_AMOUNT_CV) ** 2
        kappa_a = min(float(len(hist_amounts)), _AMOUNT_PRIOR_STRENGTH)
        kappa_n = kappa_a + n_cur
        m_post = (kappa_a * m0 + sum(cur_amounts)) / kappa_n
        var_mu = sigma2 / kappa_n

        exp_r = exp_n * m_post
        var_r = exp_n * (sigma2 + var_mu) + var_n * m_post**2
    else:
        # No purchases anywhere: nothing to learn from, forecast flat.
        m_post = exp_r = var_r = 0.0

    sd_r = math.sqrt(var_r)
    predicted_total = round(current["total"] + exp_r, 2)
    headroom = budget - current["total"] - exp_r
    if sd_r > 0:
        prob_over = 1.0 - _norm_cdf(headroom / sd_r)
    else:
        prob_over = 1.0 if headroom < 0 else 0.0

    return {
        "has_prediction": True,
        "method": "bayesian",
        "days_elapsed": days_elapsed,
        "days_remaining": days_remaining,
        "days_in_month": days_in_period,
        # Posterior expected daily spend going forward
        "avg_daily_rate": round((alpha / beta) * m_post, 2),
        # Posterior forecast
        "predicted_total": predicted_total,
        "predicted_remaining": round(budget - predicted_total, 2),
        "ci_level": _CI_LEVEL,
        "ci_low_total": round(current["total"] + max(0.0, exp_r - _CI_Z_90 * sd_r), 2),
        "ci_high_total": round(current["total"] + exp_r + _CI_Z_90 * sd_r, 2),
        "prob_over_budget": round(prob_over * 100, 1),
        "will_exceed_budget": prob_over >= 0.5,
        "expected_purchases_remaining": round(exp_n, 1),
        "posterior_mean_per_purchase": round(m_post, 2),
        # Trip details (display only)
        "grocery": _with_trip_projection(
            trip_interval_stats("Lebensmittel", _TRIP_LOOKBACK_DAYS, today), today, p_end
        ),
        "fuel": _with_trip_projection(
            trip_interval_stats("Tanken", _TRIP_LOOKBACK_DAYS, today), today, p_end
        ),
    }


# ── Statistics page ───────────────────────────────────────────────────────────

def get_yearly_comparison() -> dict:
    """Per-period totals arranged by year for a grouped bar chart.

    Returns:
        ``{"years": [2022, ...], "data": {2022: [period 1 total, ..., period 12 total]}}``
    """
    lookup: dict[int, dict[int, float]] = {}
    for (py, pm), total in _period_totals().items():
        lookup.setdefault(py, {})[pm] = total

    years = sorted(lookup)
    data = {y: [round(lookup[y].get(m, 0.0), 2) for m in range(1, 13)] for y in years}
    return {"years": years, "data": data}


def get_category_trends() -> list[dict]:
    """Category totals per period for a stacked bar chart.

    Returns:
        ``[{"label", "year", "month", <category>: total, ...}, ...]`` in period order.
    """
    points: dict[tuple[int, int], dict[str, float]] = {}
    for row in _dated_expense_rows():
        cats = points.setdefault(period_for_date(row.date), {})
        cat = row.category or DEFAULT_CATEGORY
        cats[cat] = cats.get(cat, 0.0) + row.amount

    result = []
    for (y, m), cats in sorted(points.items()):
        entry: dict = {"label": f"{m:02d}/{y}", "year": y, "month": m}
        entry.update({k: round(v, 2) for k, v in cats.items()})
        result.append(entry)
    return result


def get_top_months(limit: int = 5, best: bool = False) -> list[dict]:
    """Periods with the largest budget surplus (``best=True``) or overspend."""
    results: list[dict] = []
    for (y, m), raw_total in _period_totals().items():
        total = round(raw_total, 2)
        budget = budget_for_month(y, m)
        surplus = round(budget - total, 2)
        results.append({
            "year": y,
            "month": m,
            "label": f"{m:02d}/{y}",
            "total": total,
            "budget": budget,
            "surplus": surplus,
            "over_budget": surplus < 0,
        })
    results.sort(key=lambda x: x["surplus"], reverse=best)
    return results[:limit]


def get_overall_stats() -> dict:
    """Headline numbers across all data (undated rows count towards totals only)."""
    total_spend = float(db.session.query(func.sum(Expense.amount)).scalar() or 0.0)
    total_count = db.session.query(func.count(Expense.id)).scalar() or 0

    period_totals = _period_totals()
    months_tracked = len(period_totals)
    monthly = list(period_totals.values())
    over_budget_count = sum(
        1 for (y, m), total in period_totals.items() if total > budget_for_month(y, m)
    )

    return {
        "total_spend": round(total_spend, 2),
        "total_count": total_count,
        "months_tracked": months_tracked,
        "avg_monthly": round(sum(monthly) / months_tracked, 2) if months_tracked else 0.0,
        "max_monthly": round(max(monthly), 2) if monthly else 0.0,
        "min_monthly": round(min(monthly), 2) if monthly else 0.0,
        "avg_per_trip": round(total_spend / total_count, 2) if total_count else 0.0,
        "over_budget_count": over_budget_count,
        "on_budget_pct": (
            round((1 - over_budget_count / months_tracked) * 100, 1) if months_tracked else 0
        ),
    }
