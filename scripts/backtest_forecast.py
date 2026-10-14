"""Backtest the end-of-period forecast (``app.stats.get_prediction``) on real data.

For every completed budget period and several cut-off days within it, a
temporary database is filled with only the expenses known on that day, the
forecast is computed as the dashboard would have shown it, and the result is
scored against the period's actual final total.

The real database is opened read-only and never modified.

Usage (PowerShell, project root)::

    python scripts\\backtest_forecast.py
    python scripts\\backtest_forecast.py --db data\\expenses.db --cutoffs 3 10 17 24

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
import logging
import sqlite3
import sys
import tempfile
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

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


def run(db_path: Path, cutoff_days: tuple[int, ...]) -> None:
    """Run the backtest and log a summary table.

    Args:
        db_path: Real database to read from.
        cutoff_days: Days elapsed in each period at which to forecast.
    """
    expenses, budgets = load_real_data(db_path)
    if not expenses:
        logger.error("No dated expenses in %s", db_path)
        return

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

            records: list[dict] = []
            for label in labels:
                p_start, p_end = s.period_bounds(*label)
                for day in cutoff_days:
                    today = p_start + timedelta(days=day - 1)
                    if today >= p_end:
                        continue
                    Expense.query.delete()
                    known = [e for e in expenses if e["date"] <= today]
                    if known:
                        db.session.execute(insert(Expense), known)
                    db.session.commit()

                    p = s.get_prediction(*label, today=today)
                    y = actual[label]
                    budget = s.budget_for_month(*label)
                    records.append(
                        {
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

    _report(records, len(labels))


def _report(records: list[dict], n_periods: int) -> None:
    """Log the metrics overall and per cut-off day."""
    if not records:
        logger.error("Not enough complete periods to backtest.")
        return
    level = records[0]["level"]
    alpha = 1 - level / 100
    logger.info("Periods: %d, forecasts: %d, nominal level: %d%%", n_periods, len(records), level)
    header = (
        f"{'cut-off':>8} {'n':>4} {'coverage':>9} {'below':>6} {'above':>6} {'width':>8} "
        f"{'int.score':>10} {'MAE':>7} {'bias':>7} {'Brier':>6}"
    )
    logger.info(header)
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        groups[f"day {r['day']}"].append(r)
    groups["all"] = records
    for name, rs in groups.items():
        n = len(rs)
        cov = sum(r["lo"] <= r["y"] <= r["hi"] for r in rs) / n
        below = sum(r["y"] < r["lo"] for r in rs) / n
        above = sum(r["y"] > r["hi"] for r in rs) / n
        bias = sum(r["point"] - r["y"] for r in rs) / n
        width = sum(r["hi"] - r["lo"] for r in rs) / n
        isc = sum(interval_score(r["lo"], r["hi"], r["y"], alpha) for r in rs) / n
        mae = sum(abs(r["point"] - r["y"]) for r in rs) / n
        brier = sum((r["prob"] - r["over"]) ** 2 for r in rs) / n
        logger.info(
            f"{name:>8} {n:>4} {cov:>8.0%} {below:>6.0%} {above:>6.0%} {width:>8.1f} "
            f"{isc:>10.1f} {mae:>7.1f} {bias:>+7.1f} {brier:>6.3f}"
        )


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
    args = parser.parse_args()
    run(args.db, tuple(args.cutoffs))


if __name__ == "__main__":
    main()
