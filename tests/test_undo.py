"""Tests for undoing deletions and for the newest-first undo rule."""
from datetime import date

import pytest

from app import db
from app.models import ChangeLog, Expense


@pytest.fixture
def expense_id(app):
    with app.app_context():
        e = Expense(date=date(2024, 4, 2), store="Aldi", amount=12.5,
                    category="Lebensmittel", notes="Wocheneinkauf")
        db.session.add(e)
        db.session.commit()
        return e.id


def _edit(client, eid, amount):
    client.post(f"/expense/{eid}/edit", data={
        "date": "2024-04-02", "store": "Aldi", "amount": amount, "notes": "Wocheneinkauf",
    })


def _log_ids(app, op_type):
    with app.app_context():
        return [log.id for log in ChangeLog.query.filter_by(op_type=op_type).order_by(ChangeLog.id)]


def test_delete_can_be_undone_with_original_id(app, expense_id):
    client = app.test_client()
    client.post(f"/expense/{expense_id}/delete")
    with app.app_context():
        assert db.session.get(Expense, expense_id) is None

    (log_id,) = _log_ids(app, "delete")
    client.post(f"/expense/undo/{log_id}")

    with app.app_context():
        restored = db.session.get(Expense, expense_id)
        assert restored is not None
        assert (restored.store, restored.amount, restored.date, restored.notes) == (
            "Aldi", 12.5, date(2024, 4, 2), "Wocheneinkauf"
        )


def test_older_edit_cannot_be_undone_before_newer_one(app, expense_id):
    client = app.test_client()
    _edit(client, expense_id, "20")
    _edit(client, expense_id, "30")
    first, second = _log_ids(app, "edit")

    client.post(f"/expense/undo/{first}")  # blocked: would overwrite the 30
    with app.app_context():
        assert db.session.get(Expense, expense_id).amount == 30.0
        assert db.session.get(ChangeLog, first).undone is False

    client.post(f"/expense/undo/{second}")
    client.post(f"/expense/undo/{first}")
    with app.app_context():
        assert db.session.get(Expense, expense_id).amount == 12.5


def test_edit_undo_blocked_after_delete(app, expense_id):
    client = app.test_client()
    _edit(client, expense_id, "20")
    client.post(f"/expense/{expense_id}/delete")
    (edit_log,) = _log_ids(app, "edit")

    client.post(f"/expense/undo/{edit_log}")
    with app.app_context():
        assert db.session.get(Expense, expense_id) is None
        assert db.session.get(ChangeLog, edit_log).undone is False


def test_unrelated_changes_do_not_block_undo(app, expense_id):
    with app.app_context():
        other = Expense(date=date(2024, 4, 3), store="Lidl", amount=5.0, category="Lebensmittel")
        db.session.add(other)
        db.session.commit()
        other_id = other.id

    client = app.test_client()
    _edit(client, expense_id, "20")
    client.post(f"/expense/{other_id}/delete")
    (edit_log,) = _log_ids(app, "edit")

    client.post(f"/expense/undo/{edit_log}")
    with app.app_context():
        assert db.session.get(Expense, expense_id).amount == 12.5
