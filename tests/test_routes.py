"""Regression tests: hand-edited URLs must degrade gracefully, never 500."""


def test_dashboard_ignores_non_numeric_params(app):
    client = app.test_client()
    assert client.get("/?year=abc&month=zz").status_code == 200


def test_dashboard_ignores_out_of_range_month(app):
    client = app.test_client()
    assert client.get("/?year=2024&month=13").status_code == 200
    assert client.get("/?year=999999&month=1").status_code == 200


def test_expenses_ignores_bad_page_and_filters(app):
    client = app.test_client()
    assert client.get("/expenses?page=abc").status_code == 200
    assert client.get("/expenses?year=abc&month=99").status_code == 200
    assert client.get("/expenses?page=-5").status_code == 200


def test_export_ignores_bad_filters(app):
    client = app.test_client()
    assert client.get("/expenses/export?year=nope&month=13").status_code == 200


def test_api_rejects_invalid_month_with_400(app):
    client = app.test_client()
    assert client.get("/api/monthly-summary?year=2024&month=13").status_code == 400
    assert client.get("/api/category-breakdown?year=2024&month=13").status_code == 400
    assert client.get("/api/store-breakdown?year=999999&month=1").status_code == 400
    assert client.get("/api/prediction?year=2024&month=13").status_code == 400


def test_api_valid_requests_still_work(app):
    client = app.test_client()
    assert client.get("/api/monthly-summary?year=2024&month=4").status_code == 200
    assert client.get("/api/category-breakdown").status_code == 200


def test_edit_rejects_empty_store(app):
    from datetime import date

    from app import db
    from app.models import Expense

    with app.app_context():
        e = Expense(date=date(2024, 4, 2), store="Aldi", amount=12.5, category="Lebensmittel")
        db.session.add(e)
        db.session.commit()
        eid = e.id

    client = app.test_client()
    resp = client.post(
        f"/expense/{eid}/edit",
        data={"date": "2024-04-02", "store": "", "amount": "12.50", "category": ""},
    )
    assert resp.status_code == 302  # redirected back with a flash, not saved

    with app.app_context():
        assert db.session.get(Expense, eid).store == "Aldi"
