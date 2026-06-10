import re
from datetime import date, datetime

import pandas as pd

from app import db
from app.models import Expense

# ---------------------------------------------------------------------------
# Store → Category mapping (case-insensitive substring match)
# ---------------------------------------------------------------------------
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
    "laden": "Sonstiges",
    # Fuel
    "tanken": "Tanken",
    # Furniture / Home
    "ikea": "Einrichtung",
    "jysk": "Einrichtung",
    # Pharmacy / Drugstore / Beauty
    "rossmann": "Drogerie",
    "dm": "Drogerie",
    "müller": "Drogerie",
    "muller": "Drogerie",
    "bravo": "Drogerie",
    "rituals": "Drogerie",
    # Online
    "amazon": "Online",
    # Eating out / Entertainment
    "ausgehen": "Ausgehen",
    "italiener": "Ausgehen",
    "subway": "Ausgehen",
    "türke": "Ausgehen",
    "turke": "Ausgehen",
    "pommes": "Ausgehen",
    "restaurant": "Ausgehen",
    "freibad": "Ausgehen",
    # Hardware / DIY
    "obi": "Baumarkt",
    "toom": "Baumarkt",
    "wez": "Baumarkt",
    "pollmeier": "Lebensmittel",
    # Misc shops
    "action": "Sonstiges",
    # Car-related (not fuel)
    "waschstraße": "Auto",
    "waschstrasse": "Auto",
    "parken": "Auto",
    "h2o": "Ausgehen",
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
        df = pd.read_csv(
            csv_path,
            header=0,
            names=["date", "store", "amount", "surplus", "detail", "c6", "c7", "c8", "c9"],
            dtype=str,
            keep_default_na=False,
        )
    except (OSError, ValueError, pd.errors.ParserError) as exc:
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

    for _, row in df.iterrows():
        store = row.get("store", "").strip()
        amount_raw = row.get("amount", "").strip()
        detail = row.get("detail", "").strip()

        # Skip header row or empty rows
        if not store or store.lower() == "laden" or not amount_raw:
            skipped += 1
            continue

        try:
            amount = float(amount_raw.replace(",", "."))
        except ValueError:
            skipped += 1
            continue

        date_val = _parse_date(row.get("date", ""))
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
