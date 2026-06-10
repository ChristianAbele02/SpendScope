"""Tests for the budget-period date arithmetic."""
from datetime import date

from app import stats as s


def test_calendar_months_when_start_day_is_one(ctx):
    ctx.config["PERIOD_START_DAY"] = 1
    assert s.period_for_date(date(2023, 4, 2)) == (2023, 4)
    assert s.period_bounds(2023, 2) == (date(2023, 2, 1), date(2023, 2, 28))
    assert s.period_bounds(2024, 2) == (date(2024, 2, 1), date(2024, 2, 29))


def test_shifted_period_with_start_day_seven(ctx):
    ctx.config["PERIOD_START_DAY"] = 7
    # 2 April falls in the March period (7 Mar – 6 Apr).
    assert s.period_for_date(date(2023, 4, 2)) == (2023, 3)
    assert s.period_for_date(date(2023, 4, 7)) == (2023, 4)
    start, end = s.period_bounds(2023, 3)
    assert start == date(2023, 3, 7)
    assert end == date(2023, 4, 6)


def test_january_rolls_back_to_previous_december(ctx):
    ctx.config["PERIOD_START_DAY"] = 7
    assert s.period_for_date(date(2023, 1, 3)) == (2022, 12)


def test_period_start_day_is_clamped(ctx):
    # Out-of-range values must clamp to 28 so date() never raises in February.
    ctx.config["PERIOD_START_DAY"] = 31
    assert s._period_start_day() == 28
    start, _ = s.period_bounds(2023, 2)
    assert start == date(2023, 2, 28)

    ctx.config["PERIOD_START_DAY"] = 0
    assert s._period_start_day() == 1
