"""SQLAlchemy models."""
import json
from datetime import UTC, datetime

from app import db


def _utcnow() -> datetime:
    """Timezone-aware UTC timestamp (replaces the deprecated datetime.utcnow)."""
    return datetime.now(UTC)


def _load_json(raw: str | None, default):
    """Decode a JSON text column, returning ``default`` for empty or corrupt values."""
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


class Expense(db.Model):
    """One purchase. Positive amounts are spend, negative amounts are refunds."""

    __tablename__ = "expenses"

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=True)
    store = db.Column(db.String(100), nullable=False)
    # Legacy CSV rows keep the real shop here, e.g. store="Sonstige", store_detail="OBI".
    store_detail = db.Column(db.String(200), nullable=True)
    amount = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50), nullable=True)
    notes = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow)

    @property
    def display_store(self) -> str:
        """The name to show and match on: ``store_detail`` if set, else ``store``."""
        return self.store_detail or self.store

    def to_dict(self) -> dict:
        """Serialise for the JSON export."""
        return {
            "id": self.id,
            "date": self.date.strftime("%d/%m/%Y") if self.date else None,
            "date_iso": self.date.isoformat() if self.date else None,
            "store": self.store,
            "store_detail": self.store_detail,
            "display_store": self.display_store,
            "amount": self.amount,
            "category": self.category or "Sonstiges",
            "notes": self.notes,
        }


class ReceiptSample(db.Model):
    """A reference receipt image uploaded for a store, used to learn OCR parsing rules."""

    __tablename__ = "receipt_samples"

    id = db.Column(db.Integer, primary_key=True)
    store = db.Column(db.String(100), nullable=False, index=True)
    image_filename = db.Column(db.String(255), nullable=False)
    ocr_text = db.Column(db.Text, nullable=True)
    notes = db.Column(db.String(500), nullable=True)
    uploaded_at = db.Column(db.DateTime, default=_utcnow)

    # What the parser extracted from this specific sample
    extracted_amount = db.Column(db.Float, nullable=True)
    extracted_date = db.Column(db.Date, nullable=True)
    total_keyword_found = db.Column(db.String(50), nullable=True)
    amount_on_next_line = db.Column(db.Boolean, nullable=True)


class StoreProfile(db.Model):
    """Learned OCR parsing rules for a store, rebuilt whenever its samples change."""

    __tablename__ = "store_profiles"

    id = db.Column(db.Integer, primary_key=True)
    store = db.Column(db.String(100), nullable=False, unique=True)
    total_keywords_json = db.Column(db.Text, default="[]")  # JSON list, priority order
    amount_next_line = db.Column(db.Boolean, default=False)
    sample_count = db.Column(db.Integer, default=0)
    last_updated = db.Column(db.DateTime, default=_utcnow)

    @property
    def total_keywords(self) -> list:
        """Decoded ``total_keywords_json`` (empty list if corrupt)."""
        return _load_json(self.total_keywords_json, [])


class ChangeLog(db.Model):
    """Audit entry for an edit, bulk category change or deletion, used by undo.

    Payload shapes:
        edit:   ``{"expense_id": N, "old": {...fields}, "new": {...fields}}``
        bulk:   ``{"store": "X", "old_category": "A", "new_category": "B",
                   "affected_ids": [1, 2, ...]}``
        delete: ``{"expense_id": N, "old": {...fields}}``
    """

    __tablename__ = "change_log"

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=_utcnow, index=True)
    op_type = db.Column(db.String(20), nullable=False)  # "edit" | "bulk" | "delete"
    description = db.Column(db.String(300), nullable=True)
    payload_json = db.Column(db.Text, nullable=False)
    undone = db.Column(db.Boolean, default=False)

    @property
    def payload(self) -> dict:
        """Decoded ``payload_json`` (empty dict if corrupt)."""
        return _load_json(self.payload_json, {})


class CategoryBudget(db.Model):
    """Optional monthly spending limit for one category."""

    __tablename__ = "category_budgets"

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50), nullable=False, unique=True)
    monthly_limit = db.Column(db.Float, nullable=False)

    def to_dict(self) -> dict:
        """Serialise for JSON responses."""
        return {"id": self.id, "category": self.category, "monthly_limit": self.monthly_limit}


class StoreAlias(db.Model):
    """Maps a raw store name (as stored) to a canonical display name."""

    __tablename__ = "store_aliases"

    id = db.Column(db.Integer, primary_key=True)
    alias = db.Column(db.String(100), nullable=False, unique=True)  # raw name
    canonical = db.Column(db.String(100), nullable=False)  # display name


class BudgetPeriod(db.Model):
    """One budget rule. The newest rule whose ``effective_from`` is on or before
    a period's start date defines that period's budget."""

    __tablename__ = "budget_periods"

    id = db.Column(db.Integer, primary_key=True)
    effective_from = db.Column(db.Date, nullable=False, unique=True)
    monthly_budget = db.Column(db.Float, nullable=False)
    note = db.Column(db.String(200), nullable=True)

    def to_dict(self) -> dict:
        """Serialise for JSON responses."""
        return {
            "id": self.id,
            "effective_from": self.effective_from.isoformat(),
            "monthly_budget": self.monthly_budget,
            "note": self.note or "",
        }
