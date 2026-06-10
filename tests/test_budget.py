"""Tests for budget resolution against the seeded BudgetPeriod rows."""
from app import stats as s


def test_budget_resolves_most_recent_applicable_rule(ctx):
    ctx.config["PERIOD_START_DAY"] = 1
    # Seeded history: 300 from 2000-01-01, 400 from 2023-05-30.
    assert s.budget_for_month(2022, 1) == 300.0
    # May 2023 period starts 1 May, before the 30 May rule -> still 300.
    assert s.budget_for_month(2023, 5) == 300.0
    # June 2023 onward -> 400.
    assert s.budget_for_month(2023, 6) == 400.0
    assert s.budget_for_month(2024, 1) == 400.0


def test_budget_periods_cached_within_request(app):
    with app.test_request_context():
        first = s._budget_periods()
        second = s._budget_periods()
        # Same cached object is returned for the duration of the request.
        assert first is second
