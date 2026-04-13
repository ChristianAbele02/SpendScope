from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, flash
from app import db
from app.models import BudgetPeriod, CategoryBudget, StoreAlias
from app.parser import ALL_CATEGORIES
from app import stats as s

settings = Blueprint("settings", __name__)


# ── Budget periods ─────────────────────────────────────────────────────────────

@settings.route("/budget")
def budget():
    periods = BudgetPeriod.query.order_by(BudgetPeriod.effective_from.desc()).all()
    return render_template("settings_budget.html", periods=periods)


@settings.route("/budget/add", methods=["POST"])
def budget_add():
    date_str = request.form.get("effective_from", "").strip()
    amount_str = request.form.get("monthly_budget", "").strip()
    note = request.form.get("note", "").strip() or None

    try:
        effective_from = date.fromisoformat(date_str)
    except ValueError:
        flash("Ungültiges Datum.", "danger")
        return redirect(url_for("settings.budget"))

    try:
        monthly_budget = float(amount_str.replace(",", "."))
        if monthly_budget <= 0:
            raise ValueError
    except ValueError:
        flash("Ungültiger Betrag.", "danger")
        return redirect(url_for("settings.budget"))

    if BudgetPeriod.query.filter_by(effective_from=effective_from).first():
        flash("Für dieses Datum existiert bereits ein Eintrag.", "warning")
        return redirect(url_for("settings.budget"))

    db.session.add(BudgetPeriod(
        effective_from=effective_from,
        monthly_budget=monthly_budget,
        note=note,
    ))
    db.session.commit()
    flash(f"Budget €{monthly_budget:.0f}/Monat ab {effective_from.strftime('%d.%m.%Y')} gespeichert.", "success")
    return redirect(url_for("settings.budget"))


@settings.route("/budget/delete/<int:period_id>", methods=["POST"])
def budget_delete(period_id):
    if BudgetPeriod.query.count() <= 1:
        flash("Mindestens ein Budget-Eintrag muss vorhanden bleiben.", "warning")
        return redirect(url_for("settings.budget"))

    period = BudgetPeriod.query.get_or_404(period_id)
    db.session.delete(period)
    db.session.commit()
    flash("Budget-Eintrag gelöscht.", "success")
    return redirect(url_for("settings.budget"))


# ── Category limits ────────────────────────────────────────────────────────────

@settings.route("/category-budgets")
def category_budgets():
    limits = CategoryBudget.query.order_by(CategoryBudget.category).all()
    return render_template(
        "settings_category_budget.html",
        limits=limits,
        all_categories=ALL_CATEGORIES,
    )


@settings.route("/category-budgets/set", methods=["POST"])
def category_budget_set():
    category = request.form.get("category", "").strip()
    amount_str = request.form.get("monthly_limit", "").strip()

    if not category:
        flash("Keine Kategorie angegeben.", "danger")
        return redirect(url_for("settings.category_budgets"))

    try:
        monthly_limit = float(amount_str.replace(",", "."))
        if monthly_limit <= 0:
            raise ValueError
    except ValueError:
        flash("Ungültiger Betrag.", "danger")
        return redirect(url_for("settings.category_budgets"))

    existing = CategoryBudget.query.filter_by(category=category).first()
    if existing:
        existing.monthly_limit = monthly_limit
        flash(f"Limit für {category} auf €{monthly_limit:.0f} aktualisiert.", "success")
    else:
        db.session.add(CategoryBudget(category=category, monthly_limit=monthly_limit))
        flash(f"Limit €{monthly_limit:.0f}/Monat für {category} gesetzt.", "success")
    db.session.commit()
    return redirect(url_for("settings.category_budgets"))


@settings.route("/category-budgets/<int:limit_id>/delete", methods=["POST"])
def category_budget_delete(limit_id):
    cb = CategoryBudget.query.get_or_404(limit_id)
    db.session.delete(cb)
    db.session.commit()
    flash("Kategorie-Limit gelöscht.", "success")
    return redirect(url_for("settings.category_budgets"))


# ── Store aliases ──────────────────────────────────────────────────────────────

@settings.route("/store-aliases")
def store_aliases():
    aliases = StoreAlias.query.order_by(StoreAlias.alias).all()
    all_stores = s.get_distinct_stores()
    return render_template(
        "settings_store_aliases.html",
        aliases=aliases,
        all_stores=all_stores,
    )


@settings.route("/store-aliases/add", methods=["POST"])
def store_alias_add():
    alias = request.form.get("alias", "").strip()
    canonical = request.form.get("canonical", "").strip()

    if not alias or not canonical:
        flash("Alias und Zielname müssen ausgefüllt sein.", "danger")
        return redirect(url_for("settings.store_aliases"))

    if alias == canonical:
        flash("Alias und Zielname dürfen nicht identisch sein.", "warning")
        return redirect(url_for("settings.store_aliases"))

    existing = StoreAlias.query.filter_by(alias=alias).first()
    if existing:
        existing.canonical = canonical
        flash(f"Alias '{alias}' → '{canonical}' aktualisiert.", "success")
    else:
        db.session.add(StoreAlias(alias=alias, canonical=canonical))
        flash(f"Alias '{alias}' → '{canonical}' gespeichert.", "success")
    db.session.commit()
    return redirect(url_for("settings.store_aliases"))


@settings.route("/store-aliases/<int:alias_id>/delete", methods=["POST"])
def store_alias_delete(alias_id):
    sa = StoreAlias.query.get_or_404(alias_id)
    db.session.delete(sa)
    db.session.commit()
    flash("Alias gelöscht.", "success")
    return redirect(url_for("settings.store_aliases"))
