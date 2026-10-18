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
    assert p["method"] == "bayesian-gamma"
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


def test_prediction_is_deterministic(ctx):
    """Fixed seed: reloading the dashboard must not change the forecast."""
    _seed_history()
    _add(date(2024, 4, 2), 35.0)
    db.session.commit()

    assert s.get_prediction(2024, 4, today=TODAY) == s.get_prediction(2024, 4, today=TODAY)


def test_interval_never_below_spend_so_far(ctx):
    _seed_history()
    _add(date(2024, 4, 2), 120.0)
    _add(date(2024, 4, 4), -10.0)  # refund lowers the spend so far
    db.session.commit()

    p = s.get_prediction(2024, 4, today=TODAY)

    assert p["ci_low_total"] >= 110.0
    assert p["ci_low_total"] <= p["predicted_total"] <= p["ci_high_total"]


def test_last_day_of_period_has_no_remaining_uncertainty(ctx):
    _seed_history()
    _add(date(2024, 4, 15), 80.0)
    db.session.commit()

    p = s.get_prediction(2024, 4, today=date(2024, 4, 30))

    assert p["days_remaining"] == 0
    assert p["predicted_total"] == p["ci_low_total"] == p["ci_high_total"] == 80.0


def test_future_dated_expenses_are_ignored(ctx):
    _seed_history()
    _add(date(2024, 4, 2), 50.0)
    db.session.commit()
    before = s.get_prediction(2024, 4, today=TODAY)

    _add(date(2024, 4, 25), 500.0)  # entered in advance, after "today"
    db.session.commit()

    assert s.get_prediction(2024, 4, today=TODAY)["predicted_total"] == before["predicted_total"]


def test_gamma_shape_recovers_known_shape():
    import numpy as np

    rng = np.random.default_rng(1)
    amounts = rng.gamma(3.0, 10.0, 5000).tolist()

    assert abs(s._gamma_shape(amounts) - 3.0) < 0.2


def test_gamma_shape_edge_cases():
    assert s._gamma_shape([42.0]) == 1.0 / s._DEFAULT_AMOUNT_CV**2
    assert s._gamma_shape([50.0, 50.0, 50.0]) == s._MAX_GAMMA_SHAPE


def test_sparse_categories_share_one_stream():
    history = {
        "Lebensmittel": [(20.0, 1.0)] * 30,
        "Tanken": [(60.0, 1.0)] * 12,
        "Baumarkt": [(40.0, 1.0)] * 2,
        "Online": [(25.0, 1.0)] * 3,
    }
    current = {"Einrichtung": [150.0]}  # no history at all → pooled as well

    streams = s._build_streams(history, 180.0, current, 10.0)

    # Lebensmittel, Tanken and one pooled stream for Baumarkt/Online/Einrichtung.
    assert len(streams) == 3


def test_profile_detects_front_loaded_spending(ctx):
    """Purchases only in the first half of each period → lower late weights."""
    for month in range(1, 13):
        for day in (1, 3, 5, 7, 9, 11, 13):
            _add(date(2023, month, day), 30.0)
    db.session.commit()

    weights = s._period_profile(2024, 1)

    assert weights[0] > 1.0 > weights[3]
    assert weights[0] > weights[2] > weights[3]


def test_profile_is_flat_without_history(ctx):
    assert s._period_profile(2024, 1) == [1.0] * s._PROFILE_BUCKETS


def test_front_loaded_history_lowers_the_forecast(ctx):
    """The same purchases, but concentrated early, predict less for the rest."""
    def seed(days: tuple[int, ...]) -> float:
        Expense.query.delete()
        for month in range(1, 4):
            for day in days:
                _add(date(2024, month, day), 50.0)
        _add(date(2024, 4, 2), 50.0)
        db.session.commit()
        return s.get_prediction(2024, 4, today=TODAY)["predicted_total"]

    spread = seed((2, 7, 12, 17, 22, 27))
    front_loaded = seed((1, 2, 3, 4, 5, 6))

    assert front_loaded < spread


def _simulated_counts(phi: float, periods: int, seed: int) -> tuple[list[dict[str, int]], list[float]]:
    """Counts of three streams sharing a Gamma(1/φ, 1/φ) monthly factor (φ = 0: none)."""
    import numpy as np

    rng = np.random.default_rng(seed)
    rates = {"a": 0.5, "b": 0.2, "c": 0.1}
    counts = []
    for _ in range(periods):
        u = rng.gamma(1 / phi, phi) if phi > 0 else 1.0
        counts.append({st: int(rng.poisson(r * u * 30)) for st, r in rates.items()})
    return counts, [30.0] * periods


def test_shared_dispersion_recovers_the_factor_variance(monkeypatch):
    monkeypatch.setattr(s, "_MAX_SHARED_DISPERSION", 0.5)  # off (0) by default
    counts, exposures = _simulated_counts(phi=0.2, periods=2000, seed=1)
    assert abs(s._shared_dispersion(counts, exposures) - 0.2) < 0.04


def test_shared_dispersion_is_near_zero_for_independent_streams():
    counts, exposures = _simulated_counts(phi=0.0, periods=2000, seed=2)
    assert s._shared_dispersion(counts, exposures) < 0.01


def test_shared_dispersion_needs_two_streams_and_periods():
    assert s._shared_dispersion([{"a": 5}, {"a": 9}], [30.0, 30.0]) == 0.0
    assert s._shared_dispersion([{"a": 5, "b": 2}], [30.0]) == 0.0
    assert s._shared_dispersion([], []) == 0.0


def test_shared_factor_correlates_stream_rates():
    import numpy as np

    streams = [s._Stream(alpha=6.0, beta=20.0, n=2, k=2.0, a=50.0, b=1000.0)] * 2
    independent = s._draw_rates(streams, 5.0, 0.0, 20_000, np.random.default_rng(0))
    shared = s._draw_rates(streams, 5.0, 0.3, 20_000, np.random.default_rng(0))
    assert abs(np.corrcoef(independent)[0, 1]) < 0.03
    assert np.corrcoef(shared)[0, 1] > 0.2


def test_shared_factor_option_keeps_the_forecast_valid(ctx, monkeypatch):
    # Front-loaded and co-moving history, then the factor forced on.
    for month in (1, 2, 3):
        for day in (2, 5, 9, 14, 20):
            _add(date(2024, month, day), 40.0)
            _add(date(2024, month, day), 30.0, store="Tanken", category="Tanken")
    _add(date(2024, 4, 3), 40.0)
    db.session.commit()
    monkeypatch.setattr(s, "_MAX_SHARED_DISPERSION", 0.5)
    result = s.get_prediction(2024, 4, today=TODAY)
    assert result["has_prediction"]
    assert result["ci_low_total"] >= 40.0
    assert result["ci_low_total"] <= result["predicted_total"] <= result["ci_high_total"]
    assert s.get_prediction(2024, 4, today=TODAY) == result  # still deterministic


def test_recency_weights_halve_per_half_life(monkeypatch):
    monkeypatch.setattr(s, "_HISTORY_HALF_LIFE_PERIODS", 2.0)
    assert s._recency_weight(1) == 1.0
    assert s._recency_weight(3) == 0.5
    assert s._recency_weight(5) == 0.25
    monkeypatch.setattr(s, "_HISTORY_HALF_LIFE_PERIODS", float("inf"))
    assert s._recency_weight(6) == 1.0


def test_recency_weighting_follows_rising_spending(ctx, monkeypatch):
    # Six history periods; the purchase amount doubles in the last three.
    for month, amount in ((10, 20.0), (11, 20.0), (12, 20.0)):
        for day in (3, 10, 17, 24):
            _add(date(2023, month, day), amount)
    for month, amount in ((1, 40.0), (2, 40.0), (3, 40.0)):
        for day in (3, 10, 17, 24):
            _add(date(2024, month, day), amount)
    db.session.commit()
    flat = s.get_prediction(2024, 4, today=TODAY)["predicted_total"]
    monkeypatch.setattr(s, "_HISTORY_HALF_LIFE_PERIODS", 1.0)
    recent = s.get_prediction(2024, 4, today=TODAY)["predicted_total"]
    assert recent > flat
