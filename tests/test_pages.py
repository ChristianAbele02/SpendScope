"""Smoke tests: every page renders with data, plus edit/undo and upload safety."""
import io
from datetime import date

import pytest

from app import db
from app.models import ChangeLog, Expense, ReceiptSample


@pytest.fixture
def seeded(app):
    """App with a few expenses, including a legacy 'Sonstige' row and a refund."""
    today = date.today()
    with app.app_context():
        db.session.add_all([
            Expense(date=today, store="Aldi", amount=20.0, category="Lebensmittel"),
            Expense(date=today, store="Sonstige", store_detail="OBI", amount=35.5,
                    category="Baumarkt"),
            Expense(date=today, store="Lidl", amount=-1.85, category="Lebensmittel"),
            Expense(date=None, store="Tanken", amount=60.0, category="Tanken"),
        ])
        db.session.commit()
    return app


@pytest.mark.parametrize("url", [
    "/", "/expenses", "/statistics", "/add", "/import", "/scan/", "/scan/qr",
    "/scan/samples", "/settings/budget", "/settings/category-budgets",
    "/settings/store-aliases", "/expenses/export", "/expenses/export?format=json",
    "/api/yearly-comparison", "/api/category-trends", "/api/overall-stats",
    "/api/monthly-trends",
])
@pytest.mark.parametrize("lang", ["de", "en"])
def test_pages_render(seeded, url, lang):
    client = seeded.test_client()
    client.get(f"/lang/{lang}")
    assert client.get(url).status_code == 200


def test_edit_then_undo_restores_entry(seeded):
    with seeded.app_context():
        eid = Expense.query.filter_by(store="Aldi").one().id

    client = seeded.test_client()
    client.post(f"/expense/{eid}/edit", data={
        "date": "2024-01-02", "store": "Aldi", "amount": "99,90", "category": "Sonstiges",
    })
    with seeded.app_context():
        edited = db.session.get(Expense, eid)
        assert (edited.amount, edited.category) == (99.9, "Sonstiges")
        log_id = ChangeLog.query.one().id

    client.post(f"/expense/undo/{log_id}")
    with seeded.app_context():
        restored = db.session.get(Expense, eid)
        assert (restored.amount, restored.category, restored.date) == (
            20.0, "Lebensmittel", date.today()
        )
        assert db.session.get(ChangeLog, log_id).undone is True


def test_edit_ignores_unknown_category(seeded):
    with seeded.app_context():
        eid = Expense.query.filter_by(store="Aldi").one().id
    seeded.test_client().post(f"/expense/{eid}/edit", data={
        "store": "Aldi", "amount": "20", "category": "<script>",
    })
    with seeded.app_context():
        assert db.session.get(Expense, eid).category == "Lebensmittel"


def test_redirect_target_cannot_leave_site(seeded):
    with seeded.app_context():
        eid = Expense.query.filter_by(store="Aldi").one().id
    resp = seeded.test_client().post(f"/expense/{eid}/edit", data={
        "store": "Aldi", "amount": "20", "_next": "https://evil.example/phish",
    })
    assert resp.status_code == 302
    assert "evil.example" not in resp.headers["Location"]


def test_sample_upload_rejects_non_image_extension(app):
    resp = app.test_client().post(
        "/scan/samples/upload",
        data={"store": "Aldi", "image": (io.BytesIO(b"<script>alert(1)</script>"), "x.html")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    with app.app_context():
        assert ReceiptSample.query.count() == 0


def test_save_rejects_non_finite_amount(app):
    app.test_client().post("/scan/save", data={"store": "Aldi", "amount": "nan"})
    with app.app_context():
        assert Expense.query.count() == 0
