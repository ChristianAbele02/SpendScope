"""Analytics: budget periods, aggregations, forecast and anomaly detection.

All "month" arguments are *budget period* labels. With PERIOD_START_DAY = 7
the period labelled (2024, 3) runs from 7 March to 6 April; see period_bounds.
"""
import math
from calendar import monthrange
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TypeVar

import numpy as np
from flask import current_app, g, has_request_context
from scipy.special import betainc
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

# An expense is flagged as unusual when the posterior predictive probability of
# a purchase at least that large at the same store is below _ANOMALY_TAIL_PROB
# (see get_expense_anomalies).
_ANOMALY_TAIL_PROB = 0.01        # flag purchases rarer than 1 in 100 for their store
_ANOMALY_PRIOR_STRENGTH = 3.0    # pseudo-purchases pulling a store towards its category
_ANOMALY_MIN_CATEGORY = 5        # other purchases a category needs to set a store's prior
_ANOMALY_MIN_STORE_FOR_SHAPE = 3 # purchases a store needs to inform the shared Gamma shape

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
# The remaining spend of the period is a sum of independent purchase streams,
# one per category with enough history (sparse categories share one pooled
# stream). Groceries and fuel behave very differently (frequent and small vs.
# rare and large), so pooling them into one "typical purchase" would blur both.
# Each stream is a compound sum R_c = sum_{i=1..N_c} X_ci:
#
#   * Purchase frequency. The daily purchase rate λ_c gets a Gamma(α₀, β₀)
#     prior fitted to the last completed periods (empirical Bayes), worth the
#     history's effective days up to _FREQ_PRIOR_STRENGTH_DAYS. Posterior: Gamma(α₀ + n, β₀ + t). N_c for the rest of the
#     period is Poisson(λ_c·d) given λ_c, i.e. Negative Binomial marginally.
#
#   * Within-period profile. Spending is not spread evenly over a period (in
#     the real data, spend per day was 13.80 € in the first two weeks and
#     8.20 € in the last ten days), so t and d are not calendar days but
#     "effective days" Σ w(day), where w is a weekly profile estimated from the
#     last _PROFILE_HISTORY_PERIODS periods, shrunk towards flat (w = 1) and
#     normalised to an average of 1. A constant rate over-forecast every cut-off
#     day in the backtest.
#
#   * Purchase amount. X_ci ~ Gamma(k_c, θ_c) (shape k, rate θ). Receipts are
#     right-skewed but have a lighter upper tail than a log-normal (fuel is
#     capped by the tank size); a log-normal scored worse in the backtest
#     (interval score 366 vs. 331). The shape k_c is estimated from all
#     known amounts (Minka's closed-form approximation of the MLE, plug-in).
#     The rate gets a conjugate Gamma(a₀, b₀) prior worth κ₀ pseudo-purchases
#     at the historical mean amount (κ₀ capped at _AMOUNT_PRIOR_STRENGTH);
#     posterior: Gamma(a₀ + n·k, b₀ + Σx).
#
#   * Combination. Given N and θ, the stream total is Gamma(N·k, θ), so each
#     Monte Carlo draw needs only λ, N, θ and one Gamma variate per stream
#     (_FORECAST_DRAWS draws, fixed seed so the dashboard does not flicker).
#
# The point forecast is the posterior predictive median of the total (it
# minimises the expected absolute error), the interval uses its 5th/95th
# percentiles, and P(over budget) is the share of draws above the budget.
#
#   * Shared monthly factor. Busy months tend to be busy in every category,
#     so the streams' rates are multiplied by a common factor u ~ Gamma(1/φ,
#     1/φ) (mean 1, variance φ) for the current period. φ is estimated from
#     how strongly purchase counts co-vary across streams in past periods
#     (_shared_dispersion); φ = 0 gives independent streams. Given this
#     period's counts, λ and u are drawn with a short Gibbs sampler per draw,
#     so purchases observed in one stream also inform the others.
#     Off by default (_MAX_SHARED_DISPERSION = 0): it widened the intervals
#     and lost in the backtest on both the tuning and the holdout periods.
#     `backtest_forecast.py --tune` keeps re-testing it.
#
# The prior strengths were chosen by `backtest_forecast.py --tune` on all but
# the last 12 periods (grid search, interval score). On those 12 held-out
# periods they tied the earlier hand-picked values (interval score 290 vs. 291).
#
# Refunds (negative amounts) are excluded from the purchase model; they still
# reduce the actual spend so far. Purchase amounts of different streams are
# assumed independent.
# scripts/backtest_forecast.py scores the forecast against past periods.

_PREDICTION_HISTORY_PERIODS = 6     # completed periods used to build the priors
_FREQ_PRIOR_STRENGTH_DAYS = 180.0   # cap on prior weight (in days) for the purchase rate (tuned)
_AMOUNT_PRIOR_STRENGTH = 100.0      # cap on prior weight (in purchases) for the amount model (tuned)
_WEAK_AMOUNT_PRIOR = 1.0            # κ₀ when a stream has no history (prior from current data)
_DEFAULT_AMOUNT_CV = 0.6            # assumed coefficient of variation with <2 distinct amounts
_MAX_GAMMA_SHAPE = 1000.0           # cap on k when (nearly) all amounts are identical
_MIN_STREAM_PURCHASES = 10          # history purchases a category needs for its own stream
_VAGUE_FREQ_SHAPE = 0.5             # Jeffreys-style Gamma shape when a stream has no history
_VAGUE_FREQ_RATE = 1e-6             # Gamma rate when there is no history window at all
_CI_LEVEL = 90
_CI_TAIL_PCT = (100 - _CI_LEVEL) / 2
_FORECAST_DRAWS = 20_000
_FORECAST_SEED = 20_240_501         # fixed: identical data → identical forecast
_POOLED_STREAM = "__pooled__"
_TRIP_LOOKBACK_DAYS = 120
_PROFILE_HISTORY_PERIODS = 24       # completed periods used to estimate the profile
_PROFILE_BUCKET_DAYS = 7            # profile resolution: weeks of the period
_PROFILE_BUCKETS = 4                # days 1-7, 8-14, 15-21, 22-end
_PROFILE_PRIOR_STRENGTH = 5.0       # pseudo-purchases per bucket pulling w towards 1 (tuned)
_MAX_SHARED_DISPERSION = 0.0        # cap on φ (shared monthly factor); 0 = off, 0.5 to try it
_MIN_SHARED_DISPERSION = 1e-3       # below this the factor is dropped (independent streams)
_SHARED_FACTOR_SWEEPS = 25          # Gibbs sweeps per draw for (λ, u) given the counts


@dataclass(frozen=True)
class _Stream:
    """Posterior parameters of one purchase stream.

    Attributes:
        alpha: Gamma shape of the daily purchase rate posterior (u = 1).
        beta: Gamma rate of the daily purchase rate posterior (u = 1).
        n: Purchases observed in the current period (already in ``alpha``).
        k: Gamma shape of a single purchase amount.
        a: Gamma shape of the amount-rate (θ) posterior.
        b: Gamma rate of the amount-rate (θ) posterior.
    """

    alpha: float
    beta: float
    n: int
    k: float
    a: float
    b: float

    @property
    def mean_amount(self) -> float:
        """Posterior mean amount per purchase, E[k/θ] = k·b/(a-1)."""
        return self.k * self.b / (self.a - 1.0) if self.a > 1.0 else self.k * self.b / self.a


def _gamma_shape(amounts: list[float]) -> float:
    """Estimate the Gamma shape k from positive amounts.

    Uses Minka's (2002) closed-form approximation to the maximum-likelihood
    estimate, s = ln(mean) - mean(ln x), k ≈ (3 - s + √((s-3)² + 24s)) / 12s,
    which is accurate to about 1.5% and needs no iterative solver.
    """
    if len(amounts) < 2:
        return 1.0 / _DEFAULT_AMOUNT_CV**2
    s = math.log(sum(amounts) / len(amounts)) - sum(math.log(x) for x in amounts) / len(amounts)
    return _minka_shape(s)


def _minka_shape(s: float) -> float:
    """Gamma shape from Minka's statistic s = ln(mean) - mean(ln x)."""
    if s <= 0:  # all amounts identical
        return _MAX_GAMMA_SHAPE
    k = (3.0 - s + math.sqrt((s - 3.0) ** 2 + 24.0 * s)) / (12.0 * s)
    return min(k, _MAX_GAMMA_SHAPE)


def _within_group_gamma_shape(groups: list[list[float]], min_size: int) -> float | None:
    """Common Gamma shape of several groups that differ only in their mean.

    Pools Minka's statistic over groups with at least ``min_size`` amounts,
    s = Σ_g Σ_x (ln mean_g - ln x) / Σ_g n_g, so differences *between* groups
    (a 20 € store vs. a 60 € store) do not inflate the within-group spread.

    Returns:
        The shape, or ``None`` if no group is large enough.
    """
    total, n = 0.0, 0
    for amounts in groups:
        if len(amounts) >= min_size:
            log_mean = math.log(sum(amounts) / len(amounts))
            total += sum(log_mean - math.log(x) for x in amounts)
            n += len(amounts)
    return _minka_shape(total / n) if n else None


def _amount_posterior(hist: list[float], cur: list[float]) -> tuple[float, float, float]:
    """Gamma shape k and the posterior Gamma(a, b) of the amount rate θ.

    The prior on θ is worth κ₀ pseudo-purchases at the historical mean amount
    (empirical Bayes, κ₀ capped); without history it is centred on the current
    mean with weight _WEAK_AMOUNT_PRIOR. Caller guarantees at least one amount.

    Returns:
        ``(k, a, b)``.
    """
    k = _gamma_shape(hist + cur)
    prior = hist or cur
    m0 = sum(prior) / len(prior)
    kappa0 = min(float(len(hist)), _AMOUNT_PRIOR_STRENGTH) if hist else _WEAK_AMOUNT_PRIOR
    # Prior mean of the mean amount k/θ is k·b₀/(a₀-1) ≈ m0 for a₀ = κ₀·k.
    a0, b0 = kappa0 * k, kappa0 * m0
    return k, a0 + len(cur) * k, b0 + sum(cur)


def _profile_bucket(day_offset: int) -> int:
    """Profile bucket of a 0-based day offset within a period."""
    return min(day_offset // _PROFILE_BUCKET_DAYS, _PROFILE_BUCKETS - 1)


def _period_profile(year: int, month: int) -> list[float]:
    """Relative purchase rate per bucket of the period (average 1 per day).

    Estimated from the completed periods before (year, month) that contain at
    least one purchase. Each bucket weight is a Gamma-Poisson posterior mean
    w_j = (n_j + τ) / (E_j + τ), where E_j is the count expected under a flat
    rate and τ = _PROFILE_PRIOR_STRENGTH, so sparse history gives w ≈ 1.

    Returns:
        ``_PROFILE_BUCKETS`` weights, normalised so that a typical period has
        an exposure-weighted average weight of 1.
    """
    labels = [_prev_month(year, month, n) for n in range(1, _PROFILE_HISTORY_PERIODS + 1)]
    window_start = period_bounds(*labels[-1])[0]
    window_end = period_bounds(*labels[0])[1]
    rows = (
        db.session.query(Expense.date)
        .filter(
            Expense.date.isnot(None),
            Expense.date >= window_start,
            Expense.date <= window_end,
            Expense.amount > 0,
        )
        .all()
    )
    counts_by_period: dict[tuple[int, int], list[int]] = {}
    for row in rows:
        label = period_for_date(row.date)
        offset = (row.date - period_bounds(*label)[0]).days
        counts_by_period.setdefault(label, [0] * _PROFILE_BUCKETS)[_profile_bucket(offset)] += 1

    counts = [0] * _PROFILE_BUCKETS
    exposure = [0] * _PROFILE_BUCKETS
    for label, period_counts in counts_by_period.items():
        p_start, p_end = period_bounds(*label)
        for offset in range((p_end - p_start).days + 1):
            exposure[_profile_bucket(offset)] += 1
        counts = [c + pc for c, pc in zip(counts, period_counts, strict=True)]

    total_days = sum(exposure)
    if total_days == 0:
        return [1.0] * _PROFILE_BUCKETS
    flat_rate = sum(counts) / total_days
    tau = _PROFILE_PRIOR_STRENGTH
    weights = [
        (n + tau) / (flat_rate * d + tau) if d else 1.0
        for n, d in zip(counts, exposure, strict=True)
    ]
    norm = sum(w * d for w, d in zip(weights, exposure, strict=True)) / total_days
    return [w / norm for w in weights]


def _exposure(profile: list[float], first_offset: int, last_offset: int) -> float:
    """Effective days Σ w(day) for day offsets first_offset..last_offset (inclusive)."""
    return sum(profile[_profile_bucket(o)] for o in range(first_offset, last_offset + 1))


def _stream_key(history: dict[str, list[float]]) -> Callable[[str], str]:
    """Map a category to its stream: itself if it has enough history, else pooled."""

    def key(cat: str) -> str:
        return cat if len(history.get(cat, [])) >= _MIN_STREAM_PURCHASES else _POOLED_STREAM

    return key


def _shared_dispersion(counts: list[dict[str, int]], exposures: list[float]) -> float:
    """Method-of-moments estimate of φ, the variance of the shared monthly factor.

    Under n_sp ~ Poisson(λ_s·u_p·E_p) with E[u_p] = 1 and Var(u_p) = φ, two
    different streams s ≠ t have Cov(n_sp, n_tp) = μ_sp·μ_tp·φ, where μ_sp =
    λ_s·E_p. Hence φ̂ = Σ_p Σ_{s<t} r_sp·r_tp / Σ_p Σ_{s<t} μ_sp·μ_tp with
    residuals r = n - μ̂ and λ̂_s = Σ_p n_sp / Σ_p E_p. Only cross-stream
    products enter, so one stream's own over-dispersion (or drift) does not
    count as shared.

    Args:
        counts: Purchases per stream for each past period.
        exposures: Effective days of each of those periods.

    Returns:
        φ̂ clipped to [0, _MAX_SHARED_DISPERSION]; 0 with fewer than two
        streams or periods.
    """
    total_exposure = sum(exposures)
    streams = sorted({name for period in counts for name in period})
    if len(counts) < 2 or len(streams) < 2 or total_exposure <= 0:
        return 0.0
    rates = {st: sum(p.get(st, 0) for p in counts) / total_exposure for st in streams}
    num = den = 0.0
    for period, exposure in zip(counts, exposures, strict=True):
        mu = [rates[st] * exposure for st in streams]
        resid = [period.get(st, 0) - m for st, m in zip(streams, mu, strict=True)]
        # Σ_{s<t} x_s·x_t = ((Σx)² - Σx²) / 2
        num += (sum(resid) ** 2 - sum(r * r for r in resid)) / 2
        den += (sum(mu) ** 2 - sum(m * m for m in mu)) / 2
    if den <= 0:
        return 0.0
    return min(max(num / den, 0.0), _MAX_SHARED_DISPERSION)


def _stream_period_counts(
    year: int, month: int, key: Callable[[str], str], profile: list[float]
) -> tuple[list[dict[str, int]], list[float]]:
    """Purchases per stream and effective days of each recent completed period.

    Uses the last _PROFILE_HISTORY_PERIODS periods before (year, month) that
    contain at least one purchase.
    """
    labels = [_prev_month(year, month, n) for n in range(1, _PROFILE_HISTORY_PERIODS + 1)]
    rows = (
        db.session.query(Expense.date, Expense.category)
        .filter(
            Expense.date.isnot(None),
            Expense.date >= period_bounds(*labels[-1])[0],
            Expense.date <= period_bounds(*labels[0])[1],
            Expense.amount > 0,
        )
        .all()
    )
    by_period: dict[tuple[int, int], dict[str, int]] = {}
    for row in rows:
        stream = key(row.category or DEFAULT_CATEGORY)
        period = by_period.setdefault(period_for_date(row.date), {})
        period[stream] = period.get(stream, 0) + 1
    counts, exposures = [], []
    for label, period in by_period.items():
        p_start, p_end = period_bounds(*label)
        counts.append(period)
        exposures.append(_exposure(profile, 0, (p_end - p_start).days))
    return counts, exposures


def _build_streams(
    history: dict[str, list[float]],
    hist_exposure: float,
    current: dict[str, list[float]],
    elapsed_exposure: float,
) -> list[_Stream]:
    """Posterior streams from per-category amounts.

    Args:
        history: Positive amounts per category over the history window.
        hist_exposure: Effective days covered by the (tracked) history window.
        current: Positive amounts per category in the current period so far.
        elapsed_exposure: Effective days of the current period observed so far.

    Returns:
        One stream per category with at least _MIN_STREAM_PURCHASES history
        purchases, plus one pooled stream for all remaining categories.
    """
    key = _stream_key(history)
    hist_g: dict[str, list[float]] = {}
    cur_g: dict[str, list[float]] = {}
    for cat, amounts in history.items():
        hist_g.setdefault(key(cat), []).extend(amounts)
    for cat, amounts in current.items():
        cur_g.setdefault(key(cat), []).extend(amounts)

    kappa_f = min(hist_exposure, _FREQ_PRIOR_STRENGTH_DAYS)
    streams = []
    for name in sorted(set(hist_g) | set(cur_g)):
        h, c = hist_g.get(name, []), cur_g.get(name, [])
        if h and hist_exposure > 0:
            alpha0, beta0 = len(h) / hist_exposure * kappa_f, kappa_f
        else:
            alpha0, beta0 = _VAGUE_FREQ_SHAPE, kappa_f or _VAGUE_FREQ_RATE
        k, a, b = _amount_posterior(h, c)
        streams.append(_Stream(alpha0 + len(c), beta0 + elapsed_exposure, len(c), k, a, b))
    return streams


def _draw_rates(
    streams: list[_Stream],
    elapsed_exposure: float,
    dispersion: float,
    draws: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Posterior draws of each stream's current-period rate λ_s·u.

    Without a shared factor (φ below _MIN_SHARED_DISPERSION) these are the
    independent conjugate posteriors. Otherwise each draw runs a Gibbs sampler
    for _SHARED_FACTOR_SWEEPS sweeps, starting from u drawn from its prior:
    λ_s | u ~ Gamma(α₀_s + n_s, β₀_s + u·t) and
    u | λ ~ Gamma(1/φ + Σn_s, 1/φ + t·Σλ_s), with t the elapsed effective days.

    Returns:
        Array of shape (len(streams), draws).
    """
    alpha = np.array([[st.alpha] for st in streams])
    beta = np.array([[st.beta] for st in streams])
    if dispersion < _MIN_SHARED_DISPERSION:
        return rng.gamma(alpha, 1.0 / beta, (len(streams), draws))
    n_total = sum(st.n for st in streams)
    beta0 = beta - elapsed_exposure  # prior rate; alpha already includes n
    shape_u = 1.0 / dispersion
    u = rng.gamma(shape_u, dispersion, draws)
    for _ in range(_SHARED_FACTOR_SWEEPS):
        lam = rng.gamma(alpha, 1.0 / (beta0 + u * elapsed_exposure))
        u = rng.gamma(shape_u + n_total, 1.0 / (shape_u + elapsed_exposure * lam.sum(axis=0)))
    lam = rng.gamma(alpha, 1.0 / (beta0 + u * elapsed_exposure))
    return lam * u


def _simulate_remaining(
    streams: list[_Stream],
    elapsed_exposure: float,
    remaining_exposure: float,
    dispersion: float,
    draws: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Posterior predictive draws of the spend in the rest of the period.

    Args:
        streams: Posterior purchase streams.
        elapsed_exposure: Effective days of the period observed so far.
        remaining_exposure: Effective days left in the period.
        dispersion: Variance φ of the shared monthly factor (0 = independent).
        draws: Number of Monte Carlo draws.
        rng: Random generator (seeded by the caller).

    Returns:
        Array of ``draws`` simulated remaining totals (euros).
    """
    total = np.zeros(draws)
    if remaining_exposure <= 0 or not streams:
        return total
    rates = _draw_rates(streams, elapsed_exposure, dispersion, draws, rng)
    for st, lam in zip(streams, rates, strict=True):
        n = rng.poisson(lam * remaining_exposure)
        theta = rng.gamma(st.a, 1.0 / st.b, draws)
        # Sum of n iid Gamma(k, θ) amounts is Gamma(n·k, θ); shape 0 yields 0.
        total += rng.gamma(n * st.k, 1.0 / theta)
    return total


def _prediction_inputs(
    year: int, month: int, today: date
) -> tuple[dict[str, list[float]], list[int], dict[str, list[float]], float]:
    """Collect the data for the prediction model.

    Args:
        year: Period-year of the current period.
        month: Period-month of the current period.
        today: Reference date; current-period expenses after it are ignored.

    Returns:
        ``(history, hist_lengths, current, spent_so_far)``: positive amounts
        per category over the completed prior periods that contain at least one
        purchase (periods without any are treated as untracked, not as zero
        spend), the length in days of each of those periods, positive amounts per
        category in the current period up to ``today``, and the current
        period's net spend up to ``today`` (refunds included).
    """
    prior_periods = [
        _prev_month(year, month, n) for n in range(1, _PREDICTION_HISTORY_PERIODS + 1)
    ]
    earliest_start = period_bounds(*prior_periods[-1])[0]
    current_label = (year, month)

    rows = (
        db.session.query(Expense.date, Expense.amount, Expense.category)
        .filter(
            Expense.date.isnot(None),
            Expense.date >= earliest_start,
            Expense.date <= today,
        )
        .all()
    )

    by_period: dict[tuple[int, int], dict[str, list[float]]] = {}
    spent_so_far = 0.0
    for row in rows:
        label = period_for_date(row.date)
        if label == current_label:
            spent_so_far += row.amount
        if row.amount > 0:
            cat = row.category or DEFAULT_CATEGORY
            by_period.setdefault(label, {}).setdefault(cat, []).append(float(row.amount))

    history: dict[str, list[float]] = {}
    hist_lengths: list[int] = []
    for label in prior_periods:
        cats = by_period.get(label)
        if cats:
            pb_start, pb_end = period_bounds(*label)
            hist_lengths.append((pb_end - pb_start).days + 1)
            for cat, amounts in cats.items():
                history.setdefault(cat, []).extend(amounts)

    return history, hist_lengths, by_period.get(current_label, {}), round(spent_so_far, 2)


def get_prediction(year: int, month: int, today: date | None = None) -> dict:
    """Bayesian end-of-period forecast for the current budget period.

    Simulates the posterior predictive distribution of the end-of-period total
    from per-category Gamma-Poisson frequency and Gamma amount models (see
    the module comment above). Reports its median, a 90% credible interval and
    the probability of exceeding the budget. Trip-interval details for
    groceries and fuel are included for display only and do not enter the
    forecast.

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
    budget = budget_for_month(year, month)

    history, hist_lengths, current, spent = _prediction_inputs(year, month, today)
    profile = _period_profile(year, month)
    hist_exposure = sum(_exposure(profile, 0, length - 1) for length in hist_lengths)
    elapsed_exposure = _exposure(profile, 0, days_elapsed - 1)
    remaining_exposure = _exposure(profile, days_elapsed, days_in_period - 1)
    streams = _build_streams(history, hist_exposure, current, elapsed_exposure)
    dispersion = (
        _shared_dispersion(*_stream_period_counts(year, month, _stream_key(history), profile))
        if _MAX_SHARED_DISPERSION > 0
        else 0.0
    )

    rng = np.random.default_rng(_FORECAST_SEED)
    totals = spent + _simulate_remaining(
        streams, elapsed_exposure, remaining_exposure, dispersion, _FORECAST_DRAWS, rng
    )
    median, ci_low, ci_high = np.percentile(totals, [50.0, _CI_TAIL_PCT, 100.0 - _CI_TAIL_PCT])
    prob_over = float(np.mean(totals > budget))

    rates = [st.alpha / st.beta for st in streams]  # purchases per effective day
    total_rate = sum(rates)
    per_purchase = (
        sum(r * st.mean_amount for r, st in zip(rates, streams, strict=True)) / total_rate
        if total_rate > 0
        else 0.0
    )

    return {
        "has_prediction": True,
        "method": "bayesian-gamma",
        "days_elapsed": days_elapsed,
        "days_remaining": days_remaining,
        "days_in_month": days_in_period,
        # Median spend per remaining day implied by the forecast
        "avg_daily_rate": round((median - spent) / days_remaining, 2) if days_remaining else 0.0,
        # Posterior predictive forecast
        "predicted_total": round(float(median), 2),
        "predicted_remaining": round(budget - float(median), 2),
        "ci_level": _CI_LEVEL,
        "ci_low_total": round(float(ci_low), 2),
        "ci_high_total": round(float(ci_high), 2),
        "prob_over_budget": round(prob_over * 100, 1),
        "will_exceed_budget": prob_over >= 0.5,
        "expected_purchases_remaining": round(total_rate * remaining_exposure, 1),
        "posterior_mean_per_purchase": round(per_purchase, 2),
        "shared_dispersion": round(dispersion, 3),
        # Trip details (display only)
        "grocery": _with_trip_projection(
            trip_interval_stats("Lebensmittel", _TRIP_LOOKBACK_DAYS, today), today, p_end
        ),
        "fuel": _with_trip_projection(
            trip_interval_stats("Tanken", _TRIP_LOOKBACK_DAYS, today), today, p_end
        ),
    }


# ── Anomaly detection ─────────────────────────────────────────────────────────

def get_expense_anomalies(expenses: list[Expense]) -> dict[int, dict]:
    """Flag expenses that are unusually expensive for their store.

    Model (hierarchical Gamma, conjugate):

    * Amounts at a store are Gamma(k, θ) with shape k shared by the stores of
      a category and estimated from the spread *within* each store (stores with
      at least _ANOMALY_MIN_STORE_FOR_SHAPE purchases); categories without such
      a store use the shape pooled over all stores.
    * The store's rate θ has a Gamma(κ₀·k, κ₀·m) prior, i.e. κ₀ =
      _ANOMALY_PRIOR_STRENGTH pseudo-purchases at the category mean amount m.
      Stores with few visits are therefore judged mostly by their category,
      stores with many visits mostly by their own history (partial pooling).
    * The posterior predictive of a new amount is beta-prime, so the tail
      probability P(X ≥ x) has a closed form via the regularised incomplete beta
      function.

    Each expense is scored leave-one-out: its own amount is removed from the
    store and category statistics, so a large purchase cannot raise its own
    threshold. Only positive amounts are modelled; refunds are never flagged.

    Args:
        expenses: The expenses to score (e.g. the rows of one table page).

    Returns:
        ``{expense_id: {"tail_pct": P(X ≥ amount) in %, "typical": posterior
        mean amount at that store}}`` for every expense whose tail probability
        is below _ANOMALY_TAIL_PROB.
    """
    targets = [e for e in expenses if e.amount and e.amount > 0]
    if not targets:
        return {}

    rows = (
        db.session.query(Expense.store, Expense.store_detail, Expense.amount, Expense.category)
        .filter(Expense.amount > 0)
        .all()
    )
    alias_map = get_alias_map()
    by_store: dict[str, list[float]] = {}
    by_cat: dict[str, list[float]] = {}
    by_cat_store: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        store = _canonical_store(row.store, row.store_detail, alias_map)
        cat = row.category or DEFAULT_CATEGORY
        by_store.setdefault(store, []).append(row.amount)
        by_cat.setdefault(cat, []).append(row.amount)
        by_cat_store.setdefault(cat, {}).setdefault(store, []).append(row.amount)
    all_amounts = [row.amount for row in rows]
    global_k = _within_group_gamma_shape(
        [a for stores in by_cat_store.values() for a in stores.values()],
        _ANOMALY_MIN_STORE_FOR_SHAPE,
    ) or _gamma_shape(all_amounts)
    cat_k = {
        c: _within_group_gamma_shape(list(stores.values()), _ANOMALY_MIN_STORE_FOR_SHAPE)
        or global_k
        for c, stores in by_cat_store.items()
    }

    store_sum = {st: (len(a), sum(a)) for st, a in by_store.items()}
    cat_sum = {c: (len(a), sum(a)) for c, a in by_cat.items()}
    global_n, global_total = len(all_amounts), sum(all_amounts)

    result: dict[int, dict] = {}
    for e in targets:
        x = float(e.amount)
        store = _canonical_store(e.store, e.store_detail, alias_map)
        cat = e.category or DEFAULT_CATEGORY
        # Leave-one-out sufficient statistics (the expense is part of `rows`).
        s_n, s_total = store_sum.get(store, (1, x))
        s_n, s_total = s_n - 1, s_total - x
        c_n, c_total = cat_sum.get(cat, (1, x))
        c_n, c_total = c_n - 1, c_total - x
        if c_n >= _ANOMALY_MIN_CATEGORY:
            k, m = cat_k[cat], c_total / c_n
        elif global_n > 1:
            k, m = global_k, (global_total - x) / (global_n - 1)
        else:
            continue  # nothing to compare against

        a = _ANOMALY_PRIOR_STRENGTH * k + s_n * k
        b = _ANOMALY_PRIOR_STRENGTH * m + s_total
        # X ~ BetaPrime(k, a, scale b): P(X ≥ x) = I_{b/(b+x)}(a, k).
        tail = float(betainc(a, k, b / (b + x)))
        if tail < _ANOMALY_TAIL_PROB:
            result[e.id] = {
                "tail_pct": tail * 100,
                "typical": k * b / (a - 1.0) if a > 1.0 else k * b / a,
            }
    return result


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
