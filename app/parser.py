"""Category assignment and the one-off CSV import.

The CSV is opened read-only; importing never modifies the source file.
"""
import csv
import re
from datetime import date, datetime

from app import db
from app.models import Expense

# Store keyword → category. Matched case-insensitively on word boundaries
# (see get_category), so the order of entries here does not matter.
STORE_CATEGORY_MAP = {
    # Groceries
    "aldi": "Lebensmittel",
    "lidl": "Lebensmittel",
    "rewe": "Lebensmittel",
    "penny": "Lebensmittel",
    "kaufland": "Lebensmittel",
    "edeka": "Lebensmittel",
    "marktkauf": "Lebensmittel",
    "combi": "Lebensmittel",
    "netto": "Lebensmittel",
    "pollmeier": "Lebensmittel",
    # Fuel
    "tanken": "Tanken",
    # Furniture / home
    "ikea": "Einrichtung",
    "jysk": "Einrichtung",
    # Drugstore / beauty ("muller"/"turke"/"waschstrasse" cover OCR without umlauts)
    "rossmann": "Drogerie",
    "dm": "Drogerie",
    "müller": "Drogerie",
    "muller": "Drogerie",
    "bravo": "Drogerie",
    "rituals": "Drogerie",
    # Online
    "amazon": "Online",
    # Eating out / leisure
    "ausgehen": "Ausgehen",
    "italiener": "Ausgehen",
    "subway": "Ausgehen",
    "türke": "Ausgehen",
    "turke": "Ausgehen",
    "pommes": "Ausgehen",
    "restaurant": "Ausgehen",
    "freibad": "Ausgehen",
    "h2o": "Ausgehen",
    # Hardware / DIY
    "obi": "Baumarkt",
    "toom": "Baumarkt",
    "wez": "Baumarkt",
    # Car (not fuel)
    "waschstraße": "Auto",
    "waschstrasse": "Auto",
    "parken": "Auto",
    # Explicitly miscellaneous
    "action": "Sonstiges",
    "laden": "Sonstiges",
}

CATEGORY_COLORS = {
    "Lebensmittel": "#4CAF50",
    "Tanken":        "#FF9800",
    "Drogerie":      "#E91E63",
    "Einrichtung":   "#9C27B0",
    "Ausgehen":      "#F44336",
    "Online":        "#2196F3",
    "Baumarkt":      "#795548",
    "Auto":          "#607D8B",
    "Sonstiges":     "#9E9E9E",
}

ALL_CATEGORIES = list(CATEGORY_COLORS.keys())

DEFAULT_CATEGORY = "Sonstiges"

# Pre-compiled word-boundary matchers, longest key first so the most specific
# store name wins (e.g. "marktkauf" is tried before shorter keys).
_CATEGORY_MATCHERS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b" + re.escape(key) + r"\b", re.IGNORECASE), cat)
    for key, cat in sorted(
        STORE_CATEGORY_MAP.items(), key=lambda kv: len(kv[0]), reverse=True
    )
]


def get_category(store: str, detail: str | None = None) -> str:
    """Determine the category for a store name and optional detail.

    Matching is case-insensitive and anchored on word boundaries. Short keys
    such as ``"dm"`` therefore match only the standalone token, never substrings
    of unrelated words (``"Edmund"``, ``"Sandmann"``). The detail field is
    checked before the raw store name; the first key to match, longest first,
    wins. Returns ``DEFAULT_CATEGORY`` when nothing matches.
    """
    for needle in (detail, store):
        if not needle:
            continue
        text = needle.strip()
        for matcher, cat in _CATEGORY_MATCHERS:
            if matcher.search(text):
                return cat
    return DEFAULT_CATEGORY


def _parse_date(value) -> date | None:
    """Parse DD/MM/YYYY. Returns None for empty or placeholder values."""
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if not s or s.startswith(".") or s.startswith("…"):
        return None
    try:
        return datetime.strptime(s, "%d/%m/%Y").date()
    except ValueError:
        return None


# Column order of the historical spreadsheet export:
# Datum, Laden, Ausgaben, Überschuss, Detail (further columns are ignored).
_CSV_COLUMNS = ("date", "store", "amount", "surplus", "detail")


def _read_csv_rows(csv_path: str) -> list[dict[str, str]]:
    """Read the CSV (read-only) into dicts keyed by ``_CSV_COLUMNS``.

    The first line is treated as the header. Completely empty lines are
    dropped; short rows are padded with empty strings. ``utf-8-sig`` also
    accepts files saved with a byte-order mark (e.g. by Excel).
    """
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        next(reader, None)  # header
        return [
            dict(zip(_CSV_COLUMNS, [*fields, *[""] * len(_CSV_COLUMNS)], strict=False))
            for fields in reader
            if fields
        ]


def _dedupe_key(date_val, store: str, amount: float) -> tuple:
    """Identity used to detect a row already present in the database."""
    return (date_val, store, round(amount, 2))


def import_csv(csv_path: str, clear_existing: bool = False) -> dict:
    """Parse the raw CSV and load valid rows into the database.

    Args:
        csv_path: Path to the CSV file to import.
        clear_existing: When ``True`` every existing expense is deleted first
            (destructive full reimport). When ``False`` (the default) rows are
            appended and any row matching an existing
            ``(date, store, amount)`` entry is skipped, so manually added or
            scanned entries are never lost.

    Returns:
        A result dict with ``success`` and, on success, the counts
        ``imported``, ``skipped`` (malformed rows) and ``duplicates``
        (rows already present, append mode only).
    """
    try:
        rows = _read_csv_rows(csv_path)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        return {"success": False, "error": str(exc)}

    if clear_existing:
        Expense.query.delete()
        db.session.commit()
        existing_keys: set = set()
    else:
        existing_keys = {
            _dedupe_key(e.date, e.store, e.amount)
            for e in db.session.query(
                Expense.date, Expense.store, Expense.amount
            ).all()
        }

    imported = 0
    skipped = 0
    duplicates = 0

    for row in rows:
        store = row["store"].strip()
        amount_raw = row["amount"].strip()
        detail = row["detail"].strip()

        # Skip rows without store or amount, and repeated header rows ("Laden")
        if not store or store.lower() == "laden" or not amount_raw:
            skipped += 1
            continue

        try:
            amount = float(amount_raw.replace(",", "."))
        except ValueError:
            skipped += 1
            continue

        date_val = _parse_date(row["date"])
        detail_val = detail if detail and detail.lower() != "nan" else None

        key = _dedupe_key(date_val, store, amount)
        if key in existing_keys:
            duplicates += 1
            continue

        expense = Expense(
            date=date_val,
            store=store,
            store_detail=detail_val,
            amount=amount,
            category=get_category(store, detail_val),
        )
        db.session.add(expense)
        existing_keys.add(key)
        imported += 1

    db.session.commit()
    return {
        "success": True,
        "imported": imported,
        "skipped": skipped,
        "duplicates": duplicates,
    }
