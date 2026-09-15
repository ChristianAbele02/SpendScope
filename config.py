"""Application configuration.

Every setting can be overridden with an environment variable of the same name,
so the defaults below only need editing for a permanent local change.
"""
import os
from datetime import date

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
MIGRATIONS_DIR = os.path.join(BASE_DIR, "migrations")


class Config:
    """Default Flask configuration for SpendScope."""

    # Only signs the session cookie (language choice). Set SECRET_KEY in the
    # environment whenever the app is reachable from other devices.
    SECRET_KEY = os.environ.get("SECRET_KEY") or "spendscope-dev-key-change-in-prod"
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or (
        "sqlite:///" + os.path.join(DATA_DIR, "expenses.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Historical CSV read by /import and `flask import-csv`. It is only ever read.
    CSV_PATH = os.path.join(BASE_DIR, "expenses_raw.csv")

    # Reference receipt images uploaded at /scan/samples.
    RECEIPT_SAMPLES_DIR: str = os.environ.get(
        "RECEIPT_SAMPLES_DIR", os.path.join(DATA_DIR, "receipt_samples")
    )

    # Path to the Tesseract binary (default location of the UB Mannheim Windows
    # installer). If the file does not exist, pytesseract falls back to PATH.
    TESSERACT_CMD = os.environ.get(
        "TESSERACT_CMD",
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    )

    # Receipt extraction backend for /scan/process:
    #   "auto"      – use the Claude vision API when ANTHROPIC_API_KEY is set,
    #                 otherwise fall back to local Tesseract OCR (default).
    #   "claude"    – always try the Claude API first (still falls back on error).
    #   "tesseract" – never call the API; local OCR only.
    RECEIPT_EXTRACTION_BACKEND: str = os.environ.get("RECEIPT_EXTRACTION_BACKEND", "auto")

    # Model used by the Claude vision backend. claude-haiku-4-5 is a cheaper
    # alternative if extraction cost matters more than accuracy.
    RECEIPT_EXTRACTION_MODEL: str = os.environ.get(
        "RECEIPT_EXTRACTION_MODEL", "claude-opus-4-8"
    )

    # Day of month on which a new budget period starts (1 = calendar months).
    # With 7, the "March" period runs 7 Mar – 6 Apr, so an expense on 2 April
    # counts towards March. Clamped to 1–28 by app.stats._period_start_day().
    PERIOD_START_DAY: int = int(os.environ.get("PERIOD_START_DAY", 7))

    # Largest accepted upload (receipt images); 16 MiB is ample for a phone photo.
    MAX_CONTENT_LENGTH: int = int(os.environ.get("MAX_CONTENT_LENGTH", 16 * 1024 * 1024))

    # Werkzeug debugger / reloader. Off by default because the app binds 0.0.0.0
    # for phone scanning and the debugger allows arbitrary code execution.
    DEBUG: bool = os.environ.get("FLASK_DEBUG", "0") == "1"

    # Seed for the BudgetPeriod table on first run: (effective_from, monthly_budget).
    # A rule applies to every period whose start date is >= effective_from, so a
    # date that falls mid-period takes effect from the following period.
    BUDGET_HISTORY = [
        (date(2023, 5, 30), 400.0),
        (date(2000, 1, 1), 300.0),  # oldest rule, covers all earlier data
    ]
