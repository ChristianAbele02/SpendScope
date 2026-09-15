"""Tests for the Tesseract text parser (no OCR binary needed)."""
from datetime import date

from app.routes.scan import _parse_amount, _parse_date_from_text, _parse_receipt

RECEIPT = """\
ALDI SUED
Musterstrasse 1
Milch            1,19
Brot             2,49
SUMME EUR        3,68
BAR             10,00
RUECKGELD        6,32
14.07.2026 18:03
"""


def test_amount_prefers_total_keyword_over_largest_value():
    amount, keyword, next_line = _parse_amount(RECEIPT)
    assert amount == 3.68
    assert keyword == "SUMME"
    assert next_line is False


def test_amount_on_next_line():
    text = "GESAMTBETRAG\n12,50\n"
    assert _parse_amount(text, prefer_next_line=True) == (12.5, "GESAMTBETRAG", True)


def test_profile_keyword_wins():
    text = "ZWISCHENSUMME 5,00\nZU ZAHLEN 4,50\n"
    amount, keyword, _ = _parse_amount(text, preferred_keywords=["ZU ZAHLEN"])
    assert (amount, keyword) == (4.5, "ZU ZAHLEN")


def test_largest_amount_fallback_without_keyword():
    assert _parse_amount("Milch 1,20\nKaese 7,80\n") == (7.8, None, False)


def test_date_parsing_rejects_old_and_future_dates():
    today = date(2026, 7, 15)
    assert _parse_date_from_text("14.07.2026", today) == date(2026, 7, 14)
    assert _parse_date_from_text("14.07.26", today) == date(2026, 7, 14)
    assert _parse_date_from_text("01.01.2020", today) is None
    assert _parse_date_from_text("16.07.2026", today) is None


def test_parse_receipt_detects_known_store(ctx):
    parsed = _parse_receipt(RECEIPT)
    assert parsed["store"] == "Aldi"
    assert parsed["store_known"] is True
    assert parsed["amount"] == 3.68
