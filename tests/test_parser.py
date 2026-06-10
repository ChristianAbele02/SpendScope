"""Tests for category mapping and CSV import."""
from app.models import Expense
from app.parser import DEFAULT_CATEGORY, get_category, import_csv


def test_get_category_known_stores():
    assert get_category("Aldi") == "Lebensmittel"
    assert get_category("Lidl Filiale") == "Lebensmittel"
    assert get_category("Rossmann") == "Drogerie"
    assert get_category("DM") == "Drogerie"
    assert get_category("Tanken") == "Tanken"
    assert get_category("OBI") == "Baumarkt"


def test_get_category_word_boundary_avoids_false_positive():
    # The 2-letter key "dm" must not match inside unrelated words.
    assert get_category("Edmund's Imbiss") == DEFAULT_CATEGORY
    assert get_category("Sandmann") == DEFAULT_CATEGORY


def test_get_category_prefers_detail_then_store():
    # Legacy rows store the real name in the detail field.
    assert get_category("Sonstige", "OBI") == "Baumarkt"


def test_get_category_default_and_empty():
    assert get_category("Völlig unbekannt XYZ") == DEFAULT_CATEGORY
    assert get_category("") == DEFAULT_CATEGORY


def test_import_appends_and_deduplicates(ctx, tmp_path):
    csv = tmp_path / "e.csv"
    csv.write_text(
        "Datum,Laden,Ausgaben,Ueberschuss,,,,,\n"
        "03/01/2022,Aldi,9.99,0,,,,,\n"
        "04/01/2022,Lidl,5.00,0,,,,,\n",
        encoding="utf-8",
    )

    first = import_csv(str(csv))
    assert first["success"] is True
    assert first["imported"] == 2
    assert Expense.query.count() == 2

    # Re-importing the same file must add nothing and report duplicates.
    second = import_csv(str(csv))
    assert second["imported"] == 0
    assert second["duplicates"] == 2
    assert Expense.query.count() == 2


def test_import_replace_wipes_first(ctx, tmp_path):
    csv = tmp_path / "e.csv"
    csv.write_text(
        "Datum,Laden,Ausgaben,Ueberschuss,,,,,\n"
        "03/01/2022,Aldi,9.99,0,,,,,\n",
        encoding="utf-8",
    )
    import_csv(str(csv))
    # A manually added row that is not in the CSV.
    from app import db
    db.session.add(Expense(store="Manual", amount=1.0, category="Sonstiges"))
    db.session.commit()
    assert Expense.query.count() == 2

    result = import_csv(str(csv), clear_existing=True)
    assert result["imported"] == 1
    assert Expense.query.count() == 1  # the manual row was wiped
