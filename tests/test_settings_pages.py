"""Settings pages: budget rules, category limits and store aliases."""
from datetime import date

from app import db
from app import stats as s
from app.models import BudgetPeriod, CategoryBudget, StoreAlias


def _html(response) -> str:
    return response.get_data(as_text=True)


# ── Budget rules ──────────────────────────────────────────────────────────────

def test_budget_page_lists_seeded_rules(app):
    html = _html(app.test_client().get("/settings/budget"))
    assert "400" in html and "300" in html


def test_add_budget_rule_changes_later_periods(app):
    client = app.test_client()
    response = client.post(
        "/settings/budget/add",
        data={"effective_from": "2025-01-01", "monthly_budget": "450,50", "note": "Gehalt"},
        follow_redirects=True,
    )
    assert "text-bg-success" in _html(response)
    with app.app_context():
        assert s.budget_for_month(2025, 3) == 450.5
        assert s.budget_for_month(2024, 6) == 400.0
        assert BudgetPeriod.query.filter_by(note="Gehalt").count() == 1


def test_budget_rule_validation(app):
    client = app.test_client()
    for data in (
        {"effective_from": "kein Datum", "monthly_budget": "400"},
        {"effective_from": "2025-01-01", "monthly_budget": "-5"},
        {"effective_from": "2025-01-01", "monthly_budget": "abc"},
    ):
        response = client.post("/settings/budget/add", data=data, follow_redirects=True)
        assert "text-bg-danger" in _html(response)
    with app.app_context():
        assert BudgetPeriod.query.count() == 2  # only the seeded rules


def test_duplicate_budget_date_is_rejected(app):
    client = app.test_client()
    data = {"effective_from": "2023-05-30", "monthly_budget": "999"}
    response = client.post("/settings/budget/add", data=data, follow_redirects=True)
    assert "text-bg-warning" in _html(response)
    with app.app_context():
        assert BudgetPeriod.query.filter_by(effective_from=date(2023, 5, 30)).one().monthly_budget == 400


def test_delete_budget_rule_keeps_the_last_one(app):
    client = app.test_client()
    with app.app_context():
        ids = [p.id for p in BudgetPeriod.query.all()]
    client.post(f"/settings/budget/delete/{ids[0]}")
    response = client.post(f"/settings/budget/delete/{ids[1]}", follow_redirects=True)
    assert "text-bg-warning" in _html(response)
    with app.app_context():
        assert BudgetPeriod.query.count() == 1


# ── Category limits ───────────────────────────────────────────────────────────

def test_set_update_and_delete_category_limit(app):
    client = app.test_client()
    assert client.get("/settings/category-budgets").status_code == 200
    client.post("/settings/category-budgets/set", data={"category": "Tanken", "monthly_limit": "120"})
    client.post("/settings/category-budgets/set", data={"category": "Tanken", "monthly_limit": "150"})
    with app.app_context():
        limit = CategoryBudget.query.filter_by(category="Tanken").one()
        assert limit.monthly_limit == 150
        assert s.get_category_limits() == {"Tanken": 150}
        limit_id = limit.id
    client.post(f"/settings/category-budgets/{limit_id}/delete")
    with app.app_context():
        assert CategoryBudget.query.count() == 0


def test_category_limit_validation(app):
    client = app.test_client()
    client.post("/settings/category-budgets/set", data={"category": "Erfunden", "monthly_limit": "50"})
    client.post("/settings/category-budgets/set", data={"category": "Tanken", "monthly_limit": "0"})
    with app.app_context():
        assert CategoryBudget.query.count() == 0


# ── Store aliases ─────────────────────────────────────────────────────────────

def test_alias_add_update_and_delete(app):
    client = app.test_client()
    client.post("/settings/store-aliases/add", data={"alias": "REWE Markt", "canonical": "Rewe"})
    client.post("/settings/store-aliases/add", data={"alias": "REWE Markt", "canonical": "REWE"})
    with app.app_context():
        alias = StoreAlias.query.one()
        assert alias.canonical == "REWE"
        alias_id = alias.id
    client.post(f"/settings/store-aliases/{alias_id}/delete")
    with app.app_context():
        assert StoreAlias.query.count() == 0


def test_alias_validation(app):
    client = app.test_client()
    client.post("/settings/store-aliases/add", data={"alias": "", "canonical": "Rewe"})
    client.post("/settings/store-aliases/add", data={"alias": "Rewe", "canonical": "Rewe"})
    with app.app_context():
        assert StoreAlias.query.count() == 0


def test_alias_merges_stores_in_statistics(ctx):
    from app.models import Expense

    db.session.add_all([
        Expense(date=date(2024, 1, 3), store="REWE Markt", amount=10.0, category="Lebensmittel"),
        Expense(date=date(2024, 1, 4), store="Rewe", amount=5.0, category="Lebensmittel"),
        StoreAlias(alias="REWE Markt", canonical="Rewe"),
    ])
    db.session.commit()
    stores = s.get_store_breakdown(2024, 1)
    assert [(row["store"], row["total"]) for row in stores] == [("Rewe", 15.0)]
