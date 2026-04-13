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
    PERIOD_START_DAY: int = int(os.environ.get("PERIOD_START_DAY", 7))

    # Budget history: (effective_from, monthly_budget)
    BUDGET_HISTORY = [
        (date(2023, 5, 30), 400.0),
        (date(2000, 1, 1), 300.0),  # default / oldest period
    ]

    @staticmethod
    def budget_for_month(year: int, month: int) -> float:
        from config import Config
        d = date(year, month, 1)
        for effective_from, budget in Config.BUDGET_HISTORY:
            if d >= effective_from:
                return budget
        return 300.0
