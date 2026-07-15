"""Tests for the Bayesian end-of-period prediction."""
from datetime import date

from app import db
from app import stats as s
from app.models import Expense

# Fixed reference date: 10 April 2024, i.e. 10 of 30 days elapsed with
# PERIOD_START_DAY = 1 (set in conftest). Budget for April 2024 is 400
# (seeded history switches from 300 to 400 on 2023-05-30).
TODAY = date(2024, 4, 10)


def _add(d: date, amount: float, store: str = "Aldi", category: str = "Lebensmittel") -> None:
    db.session.add(Expense(date=d, store=store, amount=amount, category=category))


def _seed_history() -> None:
    """Jan-Mar 2024: six 50-euro purchases per month (about 300/month)."""
    for month in (1, 2, 3):
        for day in (2, 7, 12, 17, 22, 27):
            _add(date(2024, month, day), 50.0)
    db.session.commit()


def test_no_prediction_for_past_period(ctx):
    _seed_history()
    result = s.get_prediction(2024, 3, today=TODAY)
    assert result == {"has_prediction": False}


def test_no_prediction_for_future_period(ctx):
    result = s.get_prediction(2024, 5, today=TODAY)
    assert result == {"has_prediction": False}


def test_prediction_with_regular_history(ctx):
    _seed_history()
    _add(date(2024, 4, 2), 50.0)
    _add(date(2024, 4, 7), 50.0)
    db.session.commit()

    p = s.get_prediction(2024, 4, today=TODAY)

    assert p["has_prediction"] is True
    assert p["method"] == "bayesian"
    assert p["days_elapsed"] == 10
    assert p["days_remaining"] == 20
    # Steady 300/month pace against a 400 budget: forecast near 300, low risk.
    assert 250 < p["predicted_total"] < 360
    assert p["ci_low_total"] <= p["predicted_total"] <= p["ci_high_total"]
    assert 0 <= p["prob_over_budget"] < 50
    assert p["will_exceed_budget"] is False
    assert p["posterior_mean_per_purchase"] == 50.0


def test_heavy_spending_raises_overspend_risk(ctx):
    _seed_history()
    # Five 80-euro purchases in the first nine days: 400 spent already.
    for day in (1, 3, 5, 7, 9):
        _add(date(2024, 4, day), 80.0)
    db.session.commit()

    p = s.get_prediction(2024, 4, today=TODAY)

    assert p["predicted_total"] > 400  # budget already consumed
    assert p["prob_over_budget"] > 90
    assert p["will_exceed_budget"] is True


def test_risk_is_ordered_by_spending_pace(ctx):
    """A heavier current month must produce a higher overspend probability."""
    _seed_history()
    _add(date(2024, 4, 2), 50.0)
    _add(date(2024, 4, 7), 50.0)
    db.session.commit()
    light = s.get_prediction(2024, 4, today=TODAY)

    for day in (1, 3, 5, 9):
        _add(date(2024, 4, day), 90.0)
    db.session.commit()
    heavy = s.get_prediction(2024, 4, today=TODAY)

    assert heavy["prob_over_budget"] > light["prob_over_budget"]
    assert heavy["predicted_total"] > light["predicted_total"]


def test_prediction_without_history_uses_current_period_only(ctx):
    for day in (2, 5, 8):
        _add(date(2024, 4, day), 60.0)
    db.session.commit()

    p = s.get_prediction(2024, 4, today=TODAY)

    assert p["has_prediction"] is True
    assert p["predicted_total"] > 180.0  # more spending expected after today
    assert p["ci_low_total"] <= p["predicted_total"] <= p["ci_high_total"]
    assert 0 <= p["prob_over_budget"] <= 100


def test_prediction_with_no_data_is_flat(ctx):
    p = s.get_prediction(2024, 4, today=TODAY)

    assert p["has_prediction"] is True
    assert p["predicted_total"] == 0.0
    assert p["prob_over_budget"] == 0.0
    assert p["will_exceed_budget"] is False


def test_refunds_do_not_enter_purchase_model(ctx):
    _seed_history()
    _add(date(2024, 4, 2), 50.0)
    _add(date(2024, 4, 3), -3.14)  # refund
    db.session.commit()

    p = s.get_prediction(2024, 4, today=TODAY)

    # The refund lowers the actual total but must not count as a purchase,
    # so the mean spend per purchase stays at the 50-euro level.
    assert p["posterior_mean_per_purchase"] == 50.0


def test_wider_interval_with_more_variable_amounts(ctx):
    """Higher amount variance must widen the credible interval."""
    for month in (1, 2, 3):
        for day in (2, 7, 12, 17, 22, 27):
            _add(date(2024, month, day), 50.0)
    _add(date(2024, 4, 2), 50.0)
    db.session.commit()
    narrow = s.get_prediction(2024, 4, today=TODAY)
    width_narrow = narrow["ci_high_total"] - narrow["ci_low_total"]

    # Replace history with the same totals but alternating 10/90 amounts.
    Expense.query.delete()
    for month in (1, 2, 3):
        for i, day in enumerate((2, 7, 12, 17, 22, 27)):
            _add(date(2024, month, day), 10.0 if i % 2 else 90.0)
    _add(date(2024, 4, 2), 50.0)
    db.session.commit()
    wide = s.get_prediction(2024, 4, today=TODAY)
    width_wide = wide["ci_high_total"] - wide["ci_low_total"]

    assert width_wide > width_narrow
