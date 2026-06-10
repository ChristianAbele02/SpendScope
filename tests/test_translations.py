"""Tests for translations and the currency filter."""
from flask import session

from app.translations import TRANSLATIONS, format_eur


def test_translation_keys_are_in_parity():
    de, en = set(TRANSLATIONS["de"]), set(TRANSLATIONS["en"])
    assert de == en, f"only_de={de - en}, only_en={en - de}"


def test_format_eur_german(app):
    with app.test_request_context():
        session["lang"] = "de"
        assert format_eur(1234.5) == "1.234,50 €"
        assert format_eur(-3.14) == "-3,14 €"
        assert format_eur(80, 0) == "80 €"


def test_format_eur_english(app):
    with app.test_request_context():
        session["lang"] = "en"
        assert format_eur(1234.5) == "€1,234.50"
        assert format_eur(-3.14) == "-€3.14"


def test_format_eur_handles_non_numeric(app):
    with app.test_request_context():
        session["lang"] = "de"
        assert format_eur(None) == "None"
