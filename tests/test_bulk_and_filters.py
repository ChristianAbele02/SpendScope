"""Bulk re-categorisation (incl. legacy Sonstige+detail rows), its undo, and list filters."""
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest

from app import db
from app.models import ChangeLog, Expense


@pytest.fixture
def obi_rows(app):
    """Three OBI purchases: one plain, one legacy 'Sonstige' + detail, one already moved."""
    with app.app_context():
        rows = [
            Expense(date=date(2024, 1, 5), store="OBI", amount=20.0, category="Sonstiges"),
            Expense(date=date(2024, 1, 6), store="Sonstige", store_detail="OBI", amount=30.0,
                    category="Sonstiges"),
            Expense(date=date(2024, 1, 7), store="OBI", amount=40.0, category="Einrichtung"),
            Expense(date=date(2024, 1, 8), store="Aldi", amount=9.0, category="Sonstiges"),
        ]
        db.session.add_all(rows)
        db.session.commit()
        return [r.id for r in rows]


def _categories(app, ids: list[int]) -> list[str]:
    with app.app_context():
        return [db.session.get(Expense, i).category for i in ids]


def test_edit_offers_bulk_update_for_same_store(app, obi_rows):
    response = app.test_client().post(
        f"/expense/{obi_rows[0]}/edit",
        data={"date": "2024-01-05", "store": "OBI", "amount": "20", "category": "Baumarkt",
              "_next": "/expenses"},
    )
    query = parse_qs(urlparse(response.headers["Location"]).query)
    assert query["bulk_store"] == ["OBI"]
    assert query["bulk_old_category"] == ["Sonstiges"]
    assert query["bulk_count"] == ["1"]  # the legacy Sonstige+OBI row, not the Einrichtung one


def test_bulk_update_matches_store_and_detail_but_only_old_category(app, obi_rows):
    response = app.test_client().post(
        "/expense/bulk-category",
        data={"store": "OBI", "category": "Baumarkt", "old_category": "Sonstiges", "_next": "/expenses"},
    )
    assert response.headers["Location"] == "/expenses"
    # OBI and Sonstige+OBI move; OBI in Einrichtung and Aldi stay untouched.
    assert _categories(app, obi_rows) == ["Baumarkt", "Baumarkt", "Einrichtung", "Sonstiges"]


def test_bulk_update_can_be_undone(app, obi_rows):
    client = app.test_client()
    client.post(
        "/expense/bulk-category",
        data={"store": "OBI", "category": "Baumarkt", "old_category": "Sonstiges"},
    )
    with app.app_context():
        log = ChangeLog.query.filter_by(op_type="bulk").one()
        assert sorted(log.payload["affected_ids"]) == sorted(obi_rows[:2])
        log_id = log.id
    client.post(f"/expense/undo/{log_id}")
    assert _categories(app, obi_rows) == ["Sonstiges", "Sonstiges", "Einrichtung", "Sonstiges"]


@pytest.mark.parametrize(
    "data",
    [
        {"store": "", "category": "Baumarkt", "old_category": "Sonstiges"},
        {"store": "OBI", "category": "Erfunden", "old_category": "Sonstiges"},
        {"store": "OBI", "category": "Baumarkt", "old_category": ""},
    ],
)
def test_bulk_update_rejects_invalid_requests(app, obi_rows, data):
    response = app.test_client().post("/expense/bulk-category", data=data, follow_redirects=True)
    assert "text-bg-danger" in response.get_data(as_text=True)
    assert _categories(app, obi_rows) == ["Sonstiges", "Sonstiges", "Einrichtung", "Sonstiges"]


def test_undo_twice_is_refused(app, obi_rows):
    client = app.test_client()
    client.post("/expense/bulk-category",
                data={"store": "OBI", "category": "Baumarkt", "old_category": "Sonstiges"})
    with app.app_context():
        log_id = ChangeLog.query.one().id
    client.post(f"/expense/undo/{log_id}")
    response = client.post(f"/expense/undo/{log_id}", follow_redirects=True)
    html = response.get_data(as_text=True)
    assert "text-bg-warning" in html or "text-bg-danger" in html


# ── Expense list filters ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "query, expected",
    [
        ("", 4),
        ("year=2024&month=1", 4),
        ("year=2023", 0),
        ("category=Einrichtung", 1),
        ("store=OBI", 3),           # includes the legacy Sonstige+OBI row
        ("search=sonstige", 1),
        ("year=abc&month=99", 4),   # invalid values are ignored
    ],
)
def test_expense_list_filters(app, obi_rows, query, expected):
    html = app.test_client().get(f"/expenses?{query}").get_data(as_text=True)
    assert html.count('me-1 edit-btn"') == expected  # one edit button per table row


def test_export_respects_filters(app, obi_rows):
    response = app.test_client().get("/expenses/export?category=Einrichtung&format=csv")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "40" in body and "Aldi" not in body
