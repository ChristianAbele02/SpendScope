import json
from datetime import datetime
from app import db


class Expense(db.Model):
    __tablename__ = "expenses"

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=True)
    store = db.Column(db.String(100), nullable=False)
    store_detail = db.Column(db.String(200), nullable=True)
    amount = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50), nullable=True)
    notes = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def display_store(self):
        if self.store_detail:
            return self.store_detail
        return self.store

    def to_dict(self):
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
    """A reference receipt image uploaded for a specific store, used to train OCR parsing."""
    __tablename__ = "receipt_samples"

    id            = db.Column(db.Integer, primary_key=True)
    store         = db.Column(db.String(100), nullable=False, index=True)
    image_filename= db.Column(db.String(255), nullable=False)
    ocr_text      = db.Column(db.Text, nullable=True)
    notes         = db.Column(db.String(500), nullable=True)
    uploaded_at   = db.Column(db.DateTime, default=datetime.utcnow)

    # What the parser extracted from this specific sample
    extracted_amount    = db.Column(db.Float,   nullable=True)
    extracted_date      = db.Column(db.Date,    nullable=True)
    total_keyword_found = db.Column(db.String(50), nullable=True)
    amount_on_next_line = db.Column(db.Boolean, nullable=True)


class StoreProfile(db.Model):
    """Learned OCR parsing rules for a store, rebuilt whenever samples are added/removed."""
    __tablename__ = "store_profiles"

    id                  = db.Column(db.Integer, primary_key=True)
    store               = db.Column(db.String(100), nullable=False, unique=True)
    total_keywords_json = db.Column(db.Text, default="[]")   # JSON list, priority order
    amount_next_line    = db.Column(db.Boolean, default=False)
    sample_count        = db.Column(db.Integer, default=0)
    last_updated        = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def total_keywords(self) -> list:
        try:
            return json.loads(self.total_keywords_json)
        except Exception:
            return []


class ChangeLog(db.Model):
    """Records every edit/bulk-category operation so it can be undone."""
    __tablename__ = "change_log"

    id           = db.Column(db.Integer, primary_key=True)
    timestamp    = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    op_type      = db.Column(db.String(20), nullable=False)   # 'edit' | 'bulk'
    description  = db.Column(db.String(300), nullable=True)
    payload_json = db.Column(db.Text, nullable=False)          # JSON, see below
    undone       = db.Column(db.Boolean, default=False)

    # 'edit' payload:  {"expense_id": N, "old": {...fields...}, "new": {...fields...}}
    # 'bulk' payload:  {"store": "X", "old_category": "A", "new_category": "B",
    #                   "affected_ids": [1, 2, ...]}

    @property
    def payload(self) -> dict:
        try:
            return json.loads(self.payload_json)
        except Exception:
            return {}


class CategoryBudget(db.Model):
    """Optional monthly spending limit per category."""
    __tablename__ = "category_budgets"

    id            = db.Column(db.Integer, primary_key=True)
    category      = db.Column(db.String(50), nullable=False, unique=True)
    monthly_limit = db.Column(db.Float, nullable=False)

    def to_dict(self):
        return {"id": self.id, "category": self.category, "monthly_limit": self.monthly_limit}


class StoreAlias(db.Model):
    """Maps a raw store name (as stored in the DB) to a canonical display name."""
    __tablename__ = "store_aliases"

    id        = db.Column(db.Integer, primary_key=True)
    alias     = db.Column(db.String(100), nullable=False, unique=True)   # raw name
    canonical = db.Column(db.String(100), nullable=False)                 # display name


class BudgetPeriod(db.Model):
    """One row per budget change. The row with the highest effective_from
    that is still <= the queried month defines the budget for that month."""
    __tablename__ = "budget_periods"

    id = db.Column(db.Integer, primary_key=True)
    effective_from = db.Column(db.Date, nullable=False, unique=True)
    monthly_budget = db.Column(db.Float, nullable=False)
    note = db.Column(db.String(200), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "effective_from": self.effective_from.isoformat(),
            "monthly_budget": self.monthly_budget,
            "note": self.note or "",
        }
