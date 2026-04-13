from datetime import datetime
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


def get_category(store: str, detail: str | None = None) -> str:
    """Determine category from store name and optional detail."""
    needle = (detail or store or "").lower().strip()
    for key, cat in STORE_CATEGORY_MAP.items():
        if key in needle:
            return cat
    # Fallback: check original store field too
    store_lower = (store or "").lower().strip()
    for key, cat in STORE_CATEGORY_MAP.items():
        if key in store_lower:
            return cat
    return "Sonstiges"


def _parse_date(value) -> "datetime.date | None":
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


def import_csv(csv_path: str, clear_existing: bool = True) -> dict:
    """Parse the raw CSV and load all valid rows into the database."""
    try:
        df = pd.read_csv(
            csv_path,
            header=0,
            names=["date", "store", "amount", "surplus", "detail", "c6", "c7", "c8", "c9"],
            dtype=str,
            keep_default_na=False,
        )
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    if clear_existing:
        Expense.query.delete()
        db.session.commit()

    imported = 0
    skipped = 0

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

        expense = Expense(
            date=date_val,
            store=store,
            store_detail=detail_val,
            amount=amount,
            category=get_category(store, detail_val),
        )
        db.session.add(expense)
        imported += 1

    db.session.commit()
    return {"success": True, "imported": imported, "skipped": skipped}
