"""Tests for the hierarchical Gamma anomaly detection (stats.get_expense_anomalies)."""
from datetime import date, timedelta

from app import db
from app import stats as s
from app.models import Expense, StoreAlias

START = date(2024, 1, 1)


def _add(store: str, amounts: list[float], category: str = "Lebensmittel",
         detail: str | None = None) -> list[Expense]:
    rows = [
        Expense(date=START + timedelta(days=i), store=store, store_detail=detail,
                amount=a, category=category)
        for i, a in enumerate(amounts)
    ]
    db.session.add_all(rows)
    db.session.commit()
    return rows


# Plausible grocery receipts around 20 € (varied, so the Gamma shape is finite).
_TYPICAL = [12.0, 18.5, 22.0, 15.3, 27.8, 19.9, 24.1, 16.7, 21.4, 13.9] * 3


def test_extreme_purchase_is_flagged(ctx):
    rows = _add("Aldi", _TYPICAL + [150.0])

    result = s.get_expense_anomalies(rows)

    assert list(result) == [rows[-1].id]
    flagged = result[rows[-1].id]
    assert flagged["tail_pct"] < s._ANOMALY_TAIL_PROB * 100
    assert 15 < flagged["typical"] < 25  # leave-one-out: the 150 € is excluded


def test_ordinary_purchases_are_not_flagged(ctx):
    rows = _add("Aldi", _TYPICAL)

    assert s.get_expense_anomalies(rows) == {}


def test_refunds_are_never_flagged(ctx):
    rows = _add("Aldi", _TYPICAL + [-500.0])

    assert rows[-1].id not in s.get_expense_anomalies(rows)


def test_store_with_little_history_borrows_from_its_category(ctx):
    _add("Aldi", _TYPICAL)
    # A second store with one earlier visit: judged mostly by the category.
    new_store = _add("Netto", [21.0, 140.0])

    result = s.get_expense_anomalies(new_store)

    assert new_store[1].id in result
    assert new_store[0].id not in result


def test_expensive_store_is_judged_by_its_own_history(ctx):
    """60 € is extreme at a 20 € store but normal at a store averaging 60 €."""
    _add("Aldi", _TYPICAL)
    big = _add("Kaufland", [55.0, 62.0, 58.5, 66.0, 61.0, 57.5, 64.0, 59.0, 63.5, 60.0] * 2)
    at_cheap = _add("Aldi", [60.0])

    assert s.get_expense_anomalies(big) == {}
    assert at_cheap[0].id in s.get_expense_anomalies(at_cheap)


def test_aliases_and_store_detail_are_merged(ctx):
    db.session.add(StoreAlias(alias="ALDI SÜD", canonical="Aldi"))
    db.session.commit()
    _add("Aldi", _TYPICAL[:15])
    _add("ALDI SÜD", _TYPICAL[15:])
    legacy = _add("Sonstige", [19.0, 160.0], detail="Aldi")

    result = s.get_expense_anomalies(legacy)

    assert legacy[1].id in result
    assert 15 < result[legacy[1].id]["typical"] < 25


def test_empty_input(ctx):
    assert s.get_expense_anomalies([]) == {}
