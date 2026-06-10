import os
from datetime import date

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or "spendscope-dev-key-change-in-prod"
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or (
        "sqlite:///" + os.path.join(BASE_DIR, "data", "expenses.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    CSV_PATH = os.path.join(BASE_DIR, "expenses_raw.csv")

    # Path to the Tesseract binary.
    # Windows default after UB-Mannheim installer — adjust if you installed elsewhere.
    # Set to None to rely on PATH instead.
    TESSERACT_CMD = os.environ.get(
        "TESSERACT_CMD",
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    )

    # Day of month on which a new budget period starts (1 = calendar months).
    # Example: Set to 7 if your "month" runs from the 7th to the 6th of the next month,
    # so an expense on April 2 is counted in the March period (Mar 7 – Apr 6).
    # Must be in the range 1–28 (days that exist in every month); values outside
    # this range are clamped at runtime by app.stats._period_start_day().
    PERIOD_START_DAY: int = int(os.environ.get("PERIOD_START_DAY", 7))

    # Largest accepted upload (receipt images). Protects /scan endpoints from
    # oversized requests. 16 MiB is generous for a phone photo.
    MAX_CONTENT_LENGTH: int = int(os.environ.get("MAX_CONTENT_LENGTH", 16 * 1024 * 1024))

    # Enable the Werkzeug debugger / reloader. Off by default; the app binds
    # 0.0.0.0 for phone scanning, so the debugger must not be exposed on a LAN
    # unless explicitly requested.
    DEBUG: bool = os.environ.get("FLASK_DEBUG", "0") == "1"

    # Budget history: (effective_from, monthly_budget)
    # NOTE: With PERIOD_START_DAY > 1, a budget change takes effect from the
    # first period whose start date is >= effective_from. An effective_from that
    # falls mid-period applies to the following period, not the current one.
    BUDGET_HISTORY = [
        (date(2023, 5, 30), 400.0),
        (date(2000, 1, 1), 300.0),  # default / oldest period
    ]
