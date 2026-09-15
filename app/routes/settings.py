"""Settings pages: monthly budget rules, category limits and store aliases."""
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app import db
from app import stats as s
from app.forms import parse_amount
from app.models import BudgetPeriod, CategoryBudget, StoreAlias
from app.parser import ALL_CATEGORIES
from app.translations import format_eur, t

settings = Blueprint("settings", __name__)


def _positive_amount(value: str | None) -> float | None:
    """Parse a strictly positive euro amount, or return ``None``."""
    amount = parse_amount(value)
    return amount if amount is not None and amount > 0 else None


# ── Budget periods ────────────────────────────────────────────────────────────

@settings.route("/budget")
def budget():
    """List budget rules and the form to add one."""
    periods = BudgetPeriod.query.order_by(BudgetPeriod.effective_from.desc()).all()
    return render_template("settings_budget.html", periods=periods)


@settings.route("/budget/add", methods=["POST"])
def budget_add():
    """Add a budget rule; one rule per effective date."""
    back = redirect(url_for("settings.budget"))
    try:
        effective_from = date.fromisoformat(request.form.get("effective_from", "").strip())
    except ValueError:
        flash(t("flash_invalid_date"), "danger")
        return back

    monthly_budget = _positive_amount(request.form.get("monthly_budget"))
    if monthly_budget is None:
        flash(t("flash_invalid_amount"), "danger")
        return back

    if BudgetPeriod.query.filter_by(effective_from=effective_from).first():
        flash(t("flash_budget_exists"), "warning")
        return back

    db.session.add(BudgetPeriod(
        effective_from=effective_from,
        monthly_budget=monthly_budget,
        note=request.form.get("note", "").strip() or None,
    ))
    db.session.commit()
    flash(
        t("flash_budget_saved", format_eur(monthly_budget, 0), effective_from.strftime("%d.%m.%Y")),
        "success",
    )
    return back


@settings.route("/budget/delete/<int:period_id>", methods=["POST"])
def budget_delete(period_id: int):
    """Delete a budget rule, keeping at least one."""
    if BudgetPeriod.query.count() <= 1:
        flash(t("flash_budget_keep_one"), "warning")
        return redirect(url_for("settings.budget"))

    db.session.delete(db.get_or_404(BudgetPeriod, period_id))
    db.session.commit()
    flash(t("flash_budget_deleted"), "success")
    return redirect(url_for("settings.budget"))


# ── Category limits ───────────────────────────────────────────────────────────

@settings.route("/category-budgets")
def category_budgets():
    """List category limits and the form to set one."""
    return render_template(
        "settings_category_budget.html",
        limits=CategoryBudget.query.order_by(CategoryBudget.category).all(),
        all_categories=ALL_CATEGORIES,
    )


@settings.route("/category-budgets/set", methods=["POST"])
def category_budget_set():
    """Create or update the monthly limit for a category."""
    back = redirect(url_for("settings.category_budgets"))
    category = request.form.get("category", "").strip()
    if category not in ALL_CATEGORIES:
        flash(t("flash_no_category"), "danger")
        return back

    monthly_limit = _positive_amount(request.form.get("monthly_limit"))
    if monthly_limit is None:
        flash(t("flash_invalid_amount"), "danger")
        return back

    existing = CategoryBudget.query.filter_by(category=category).first()
    if existing:
        existing.monthly_limit = monthly_limit
        flash(t("flash_limit_updated", category, format_eur(monthly_limit, 0)), "success")
    else:
        db.session.add(CategoryBudget(category=category, monthly_limit=monthly_limit))
        flash(t("flash_limit_set", format_eur(monthly_limit, 0), category), "success")
    db.session.commit()
    return back


@settings.route("/category-budgets/<int:limit_id>/delete", methods=["POST"])
def category_budget_delete(limit_id: int):
    """Remove a category limit."""
    db.session.delete(db.get_or_404(CategoryBudget, limit_id))
    db.session.commit()
    flash(t("flash_limit_deleted"), "success")
    return redirect(url_for("settings.category_budgets"))


# ── Store aliases ─────────────────────────────────────────────────────────────

@settings.route("/store-aliases")
def store_aliases():
    """List aliases and the form to add one."""
    return render_template(
        "settings_store_aliases.html",
        aliases=StoreAlias.query.order_by(StoreAlias.alias).all(),
        all_stores=s.get_distinct_stores(),
    )


@settings.route("/store-aliases/add", methods=["POST"])
def store_alias_add():
    """Create or update an alias (raw name → display name)."""
    back = redirect(url_for("settings.store_aliases"))
    alias = request.form.get("alias", "").strip()
    canonical = request.form.get("canonical", "").strip()

    if not alias or not canonical:
        flash(t("flash_alias_required"), "danger")
        return back
    if alias == canonical:
        flash(t("flash_alias_identical"), "warning")
        return back

    existing = StoreAlias.query.filter_by(alias=alias).first()
    if existing:
        existing.canonical = canonical
        flash(t("flash_alias_updated", alias, canonical), "success")
    else:
        db.session.add(StoreAlias(alias=alias, canonical=canonical))
        flash(t("flash_alias_saved", alias, canonical), "success")
    db.session.commit()
    return back


@settings.route("/store-aliases/<int:alias_id>/delete", methods=["POST"])
def store_alias_delete(alias_id: int):
    """Remove an alias."""
    db.session.delete(db.get_or_404(StoreAlias, alias_id))
    db.session.commit()
    flash(t("flash_alias_deleted"), "success")
    return redirect(url_for("settings.store_aliases"))
