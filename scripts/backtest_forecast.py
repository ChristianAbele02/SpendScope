"""Backtest the end-of-period forecast (``app.stats.get_prediction``) on real data.

For every completed budget period and several cut-off days within it, a
temporary database is filled with only the expenses known on that day, the
forecast is computed as the dashboard would have shown it, and the result is
scored against the period's actual final total.

The real database is opened read-only and never modified.

Usage (PowerShell, project root)::

    python scripts\\backtest_forecast.py
    python scripts\\backtest_forecast.py --db data\\expenses.db --cutoffs 3 10 17 24
    python scripts\\backtest_forecast.py --tune --holdout 12

Evaluation splits:
    holdout         the last ``--holdout`` periods. ``--tune`` picks the
                    hyperparameters in TUNING_GRID by interval score on the
                    earlier periods only and then reports both the chosen and
                    the shipped settings on the holdout periods.
    after freeze    periods starting after MODEL_FROZEN_ON. The model's
                    structure was designed while looking at all earlier
                    periods, so only these are truly out-of-sample; update the
                    date whenever the model changes.

Metrics:
    coverage        share of periods whose actual total lies inside the
                    credible interval (target: the nominal level, 90%)
    mean width      average interval width in euros (narrower is better
                    only at equal coverage)
    interval score  Winkler / Gneiting-Raftery interval score; proper, so it
                    rewards narrow intervals and penalises misses (lower is better)
    below / above   share of periods whose actual total fell below / above
                    the interval (each should be about half of the misses)
    MAE             mean absolute error of the point forecast
    bias            mean of (point forecast - actual); positive = too high
    Brier           mean squared error of P(over budget) against the outcome
                    (lower is better; 0.25 = coin flip)
"""
import argparse
import itertools
import logging
import platform
import sqlite3
import sys
import tempfile
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import insert  # noqa: E402

from app import create_app, db  # noqa: E402
from app import stats as s  # noqa: E402
from app.models import BudgetPeriod, Expense  # noqa: E402
from config import Config  # noqa: E402

logger = logging.getLogger("backtest")

DEFAULT_DB = PROJECT_ROOT / "data" / "expenses.db"
DEFAULT_CUTOFF_DAYS = (3, 10, 17, 24)  # days elapsed in the period at forecast time
# Skip the first periods so every forecast has a full prior history window.
WARMUP_PERIODS = 6
DEFAULT_HOLDOUT_PERIODS = 12
# Date the current model structure was fixed; periods starting later are unseen.
MODEL_FROZEN_ON = date(2026, 10, 18)
# Fewer draws while tuning keeps the grid fast; Monte Carlo error at 5,000
# draws is small next to the period-to-period differences being scored.
TUNING_DRAWS = 5_000
# Hyperparameters of app.stats searched by --tune; _MAX_SHARED_DISPERSION = 0
# switches the shared monthly factor off. A first grid (20/45/90, 10/25/50,
# 5/20/80) put the optimum at its edge, so it was widened in that direction.
# 180 days is about the whole six-period history, i.e. an uncapped prior.
TUNING_GRID: dict[str, tuple[float, ...]] = {
    "_FREQ_PRIOR_STRENGTH_DAYS": (45.0, 90.0, 180.0),
    "_AMOUNT_PRIOR_STRENGTH": (25.0, 50.0, 100.0),
    "_PROFILE_PRIOR_STRENGTH": (2.0, 5.0, 20.0),
    "_MAX_SHARED_DISPERSION": (0.0, 0.5),
}

Params = dict[str, float]
Record = dict[str, Any]


def load_real_data(db_path: Path) -> tuple[list[dict], list[tuple[date, float]]]:
    """Read dated expenses and budget rules from the real database (read-only).

    Args:
        db_path: Path to the SQLite database.

    Returns:
        ``(expenses, budget_rules)`` as plain Python values.
    """
    uri = f"file:{db_path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        expenses = [
            {
                "date": date.fromisoformat(d),
                "store": store,
                "store_detail": detail,
                "amount": float(amount),
                "category": category,
            }
            for d, store, detail, amount, category in conn.execute(
                "SELECT date, store, store_detail, amount, category FROM expenses "
                "WHERE date IS NOT NULL"
            )
        ]
        budgets = [
            (date.fromisoformat(d), float(b))
            for d, b in conn.execute("SELECT effective_from, monthly_budget FROM budget_periods")
        ]
    return expenses, budgets


def interval_score(lo: float, hi: float, y: float, alpha: float) -> float:
    """Interval score for a central (1 - alpha) interval (Gneiting & Raftery, 2007)."""
    return (hi - lo) + (2 / alpha) * max(lo - y, 0.0) + (2 / alpha) * max(y - hi, 0.0)


@contextmanager
def override(params: Params) -> Iterator[None]:
    """Temporarily set module constants of app.stats (restored on exit)."""
    saved = {name: getattr(s, name) for name in params}
    try:
        for name, value in params.items():
            setattr(s, name, value)
        yield
    finally:
        for name, value in saved.items():
            setattr(s, name, value)


def collect(
    db_path: Path, cutoff_days: tuple[int, ...], configs: list[Params]
) -> tuple[list[list[Record]], list[tuple[int, int]]]:
    """Forecast every evaluated period and cut-off day under each configuration.

    Args:
        db_path: Real database to read from.
        cutoff_days: Days elapsed in each period at which to forecast.
        configs: app.stats constant overrides; ``{}`` is the shipped model.

    Returns:
        ``(records, labels)``: one record list per configuration and the
        evaluated period labels in chronological order.
    """
    expenses, budgets = load_real_data(db_path)
    if not expenses:
        logger.error("No dated expenses in %s", db_path)
        return [[] for _ in configs], []

    records: list[list[Record]] = [[] for _ in configs]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:

        class BacktestConfig(Config):
            SQLALCHEMY_DATABASE_URI = "sqlite:///" + (Path(tmp) / "backtest.db").as_posix()
            RECEIPT_SAMPLES_DIR = str(Path(tmp) / "samples")
            TESTING = True

        app = create_app(BacktestConfig)
        logger.disabled = False  # Alembic's fileConfig disables pre-existing loggers
        with app.app_context():
            BudgetPeriod.query.delete()
            for eff, amount in budgets:
                db.session.add(BudgetPeriod(effective_from=eff, monthly_budget=amount))
            db.session.commit()

            actual: dict[tuple[int, int], float] = defaultdict(float)
            for e in expenses:
                actual[s.period_for_date(e["date"])] += e["amount"]

            last_date = max(e["date"] for e in expenses)
            labels = sorted(actual)[WARMUP_PERIODS:]
            labels = [lb for lb in labels if s.period_bounds(*lb)[1] < last_date]

            for label in labels:
                p_start, p_end = s.period_bounds(*label)
                y = actual[label]
                budget = s.budget_for_month(*label)
                for day in cutoff_days:
                    today = p_start + timedelta(days=day - 1)
                    if today >= p_end:
                        continue
                    Expense.query.delete()
                    known = [e for e in expenses if e["date"] <= today]
                    if known:
                        db.session.execute(insert(Expense), known)
                    db.session.commit()
                    for params, out in zip(configs, records, strict=True):
                        with override(params):
                            p = s.get_prediction(*label, today=today)
                        out.append(
                            {
                                "label": label,
                                "day": day,
                                "y": y,
                                "point": p["predicted_total"],
                                "lo": p["ci_low_total"],
                                "hi": p["ci_high_total"],
                                "level": p["ci_level"],
                                "prob": p["prob_over_budget"] / 100,
                                "over": 1.0 if y > budget else 0.0,
                            }
                        )
            db.session.remove()
            db.engine.dispose()  # release the file so the temp dir can be removed (Windows)
    return records, labels


def metrics(rs: list[Record]) -> dict[str, float]:
    """Coverage, misses, width, interval score, MAE, bias and Brier of a record set."""
    n = len(rs)
    alpha = 1 - float(rs[0]["level"]) / 100
    lo = [float(r["lo"]) for r in rs]
    hi = [float(r["hi"]) for r in rs]
    y = [float(r["y"]) for r in rs]
    point = [float(r["point"]) for r in rs]
    return {
        "n": n,
        "coverage": sum(a <= v <= b for a, b, v in zip(lo, hi, y, strict=True)) / n,
        "below": sum(v < a for a, v in zip(lo, y, strict=True)) / n,
        "above": sum(v > b for b, v in zip(hi, y, strict=True)) / n,
        "width": sum(b - a for a, b in zip(lo, hi, strict=True)) / n,
        "score": sum(
            interval_score(a, b, v, alpha) for a, b, v in zip(lo, hi, y, strict=True)
        ) / n,
        "mae": sum(abs(p - v) for p, v in zip(point, y, strict=True)) / n,
        "bias": sum(p - v for p, v in zip(point, y, strict=True)) / n,
        "brier": sum((float(r["prob"]) - float(r["over"])) ** 2 for r in rs) / n,
    }


HEADER = (
    f"{'':>18} {'n':>4} {'coverage':>9} {'below':>6} {'above':>6} {'width':>8} "
    f"{'int.score':>10} {'MAE':>7} {'bias':>7} {'Brier':>6}"
)


def row(name: str, rs: list[Record]) -> str:
    """One formatted table row, or a placeholder when ``rs`` is empty."""
    if not rs:
        return f"{name:>18} {0:>4}   (no periods yet)"
    m = metrics(rs)
    return (
        f"{name:>18} {m['n']:>4} {m['coverage']:>8.0%} {m['below']:>6.0%} {m['above']:>6.0%} "
        f"{m['width']:>8.1f} {m['score']:>10.1f} {m['mae']:>7.1f} {m['bias']:>+7.1f} "
        f"{m['brier']:>6.3f}"
    )


def split(
    records: list[Record], labels: list[tuple[int, int]], holdout: int
) -> tuple[list[Record], list[Record], list[Record]]:
    """Records of the tuning periods, the last ``holdout`` periods and the post-freeze ones."""
    holdout_labels = set(labels[-holdout:]) if holdout > 0 else set()
    tuning = [r for r in records if r["label"] not in holdout_labels]
    held = [r for r in records if r["label"] in holdout_labels]
    frozen = [r for r in records if s.period_bounds(*r["label"])[0] > MODEL_FROZEN_ON]
    return tuning, held, frozen


def report(records: list[Record], labels: list[tuple[int, int]], holdout: int) -> None:
    """Log the shipped model's metrics per cut-off day and per evaluation split."""
    if not records:
        logger.error("Not enough complete periods to backtest.")
        return
    logger.info(
        "Periods: %d, forecasts: %d, nominal level: %s%%",
        len(labels), len(records), records[0]["level"],
    )
    logger.info(HEADER)
    by_day: dict[int, list[Record]] = defaultdict(list)
    for r in records:
        by_day[int(r["day"])].append(r)
    for day, rs in sorted(by_day.items()):
        logger.info(row(f"day {day}", rs))
    logger.info(row("all", records))
    tuning, held, frozen = split(records, labels, holdout)
    logger.info(row("earlier periods", tuning))
    logger.info(row(f"last {holdout}", held))
    logger.info(row("after freeze", frozen))


def tune(db_path: Path, cutoff_days: tuple[int, ...], holdout: int) -> None:
    """Choose TUNING_GRID settings on the earlier periods, then score them on the holdout.

    Every configuration is forecast on every period (one pass over the data),
    but the choice uses the interval score on the earlier periods only; the
    holdout periods are looked at only for the final comparison.
    """
    names = list(TUNING_GRID)
    configs: list[Params] = [
        {"_FORECAST_DRAWS": TUNING_DRAWS, **dict(zip(names, values, strict=True))}
        for values in itertools.product(*TUNING_GRID.values())
    ]
    shipped: Params = {"_FORECAST_DRAWS": TUNING_DRAWS, **{n: getattr(s, n) for n in names}}
    if shipped not in configs:
        configs.append(shipped)
    logger.info(
        "Tuning %d configurations (%d draws each) on all but the last %d periods ...",
        len(configs), TUNING_DRAWS, holdout,
    )
    all_records, labels = collect(db_path, cutoff_days, configs)
    if not labels or holdout >= len(labels):
        logger.error("Need more evaluated periods than --holdout (%d).", holdout)
        return

    tuning_scores = [metrics(split(rs, labels, holdout)[0])["score"] for rs in all_records]
    best = min(range(len(configs)), key=tuning_scores.__getitem__)
    ranked = sorted(range(len(configs)), key=tuning_scores.__getitem__)
    logger.info("Best five on the earlier periods (interval score):")
    for i in ranked[:5]:
        settings = ", ".join(f"{n.strip('_').lower()}={configs[i][n]:g}" for n in names)
        logger.info("  %7.1f  %s", tuning_scores[i], settings)

    shipped_i = configs.index(shipped)
    logger.info("")
    logger.info("Holdout: last %d periods (%s to %s)", holdout, labels[-holdout], labels[-1])
    logger.info(HEADER)
    for name, i in (("shipped", shipped_i), ("tuned", best)):
        logger.info(row(name, split(all_records[i], labels, holdout)[1]))
    if best == shipped_i:
        logger.info("The tuned settings equal the shipped ones.")


def main() -> None:
    """Parse arguments and run the backtest."""
    # Own handler: create_app() runs Alembic's fileConfig, which resets the root logger.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database to read")
    parser.add_argument(
        "--cutoffs", type=int, nargs="+", default=list(DEFAULT_CUTOFF_DAYS),
        help="days elapsed in the period at forecast time",
    )
    parser.add_argument(
        "--holdout", type=int, default=DEFAULT_HOLDOUT_PERIODS,
        help="number of most recent periods held out from tuning",
    )
    parser.add_argument(
        "--tune", action="store_true",
        help="grid-search the hyperparameters on the earlier periods, score on the holdout",
    )
    args = parser.parse_args()
    _log_versions()
    cutoffs = tuple(args.cutoffs)
    if args.tune:
        tune(args.db, cutoffs, args.holdout)
    else:
        records, labels = collect(args.db, cutoffs, [{}])
        report(records[0], labels, args.holdout)


def _log_versions() -> None:
    """Log the software versions and the forecast seed for reproducibility."""
    import numpy
    import scipy

    logger.info(
        "Python %s, numpy %s, scipy %s, seed %d, draws %d",
        platform.python_version(), numpy.__version__, scipy.__version__,
        s._FORECAST_SEED, s._FORECAST_DRAWS,
    )


if __name__ == "__main__":
    main()
