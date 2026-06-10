from flask import Blueprint, jsonify, request

from app import stats as s

api = Blueprint("api", __name__)


@api.route("/monthly-trends")
def monthly_trends():
    year_param = request.args.get("years")
    years = [int(y) for y in year_param.split(",") if y.strip().isdigit()] if year_param else None
    data = s.get_monthly_trends(years)
    return jsonify(data)


@api.route("/category-breakdown")
def category_breakdown():
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    data = s.get_category_breakdown(year, month)
    return jsonify(data)


@api.route("/store-breakdown")
def store_breakdown():
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    limit = request.args.get("limit", 10, type=int)
    data = s.get_store_breakdown(year, month, limit)
    return jsonify(data)


@api.route("/monthly-summary")
def monthly_summary():
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    if not year or not month:
        return jsonify({"error": "year and month required"}), 400
    data = s.get_monthly_summary(year, month)
    return jsonify(data)


@api.route("/prediction")
def prediction():
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    if not year or not month:
        return jsonify({"error": "year and month required"}), 400
    data = s.get_prediction(year, month)
    return jsonify(data)


@api.route("/yearly-comparison")
def yearly_comparison():
    data = s.get_yearly_comparison()
    return jsonify(data)


@api.route("/category-trends")
def category_trends():
    data = s.get_category_trends()
    return jsonify(data)


@api.route("/overall-stats")
def overall_stats():
    data = s.get_overall_stats()
    return jsonify(data)
