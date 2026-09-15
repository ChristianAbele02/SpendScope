"""JSON endpoints consumed by the Chart.js charts."""
from flask import Blueprint, jsonify, request

from app import stats as s

api = Blueprint("api", __name__)

# date() only accepts years in this range; values outside it are rejected so
# stats helpers never construct an invalid date (which would return HTTP 500).
_MIN_YEAR = 1
_MAX_YEAR = 9999


def _valid_year_month(year: int | None, month: int | None) -> bool:
    """True when the given year/month (each optional) can form a real date."""
    if year is not None and not _MIN_YEAR <= year <= _MAX_YEAR:
        return False
    return month is None or 1 <= month <= 12


def _year_month_args(required: bool = False):
    """Read ``year``/``month`` query args.

    Returns:
        ``(year, month, error_response)``; ``error_response`` is a
        ``(json, 400)`` tuple when the arguments are missing or invalid.
    """
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    if required and (not year or not month):
        return year, month, (jsonify({"error": "year and month required"}), 400)
    if not _valid_year_month(year, month):
        return year, month, (jsonify({"error": "invalid year or month"}), 400)
    return year, month, None


@api.route("/monthly-trends")
def monthly_trends():
    """Per-period totals vs. budget; ``?years=2023,2024`` restricts the years."""
    year_param = request.args.get("years")
    years = [int(y) for y in year_param.split(",") if y.strip().isdigit()] if year_param else None
    return jsonify(s.get_monthly_trends(years))


@api.route("/category-breakdown")
def category_breakdown():
    """Spend per category for a period, a year, or all time."""
    year, month, error = _year_month_args()
    return error or jsonify(s.get_category_breakdown(year, month))


@api.route("/store-breakdown")
def store_breakdown():
    """Top stores for a period, a year, or all time (``?limit=`` defaults to 10)."""
    year, month, error = _year_month_args()
    limit = request.args.get("limit", 10, type=int)
    return error or jsonify(s.get_store_breakdown(year, month, limit))


@api.route("/monthly-summary")
def monthly_summary():
    """Total, budget and remaining budget for one period."""
    year, month, error = _year_month_args(required=True)
    return error or jsonify(s.get_monthly_summary(year, month))


@api.route("/prediction")
def prediction():
    """Bayesian end-of-period forecast (only for the current period)."""
    year, month, error = _year_month_args(required=True)
    return error or jsonify(s.get_prediction(year, month))


@api.route("/yearly-comparison")
def yearly_comparison():
    """Per-period totals grouped by year."""
    return jsonify(s.get_yearly_comparison())


@api.route("/category-trends")
def category_trends():
    """Category totals per period."""
    return jsonify(s.get_category_trends())


@api.route("/overall-stats")
def overall_stats():
    """All-time headline numbers."""
    return jsonify(s.get_overall_stats())
