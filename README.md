# SpendScope

A personal single-user expense tracker with a web interface, configurable budgets, mobile receipt scanning, and analytics.

## Features

- **Dashboard** — monthly KPIs (spent, remaining, avg/day, transaction count), end-of-month prediction, year-over-year comparison card, category budget status bars
- **Expense list** — paginated table with filters (year, month, category, store, free-text search), inline editing, bulk category updates, undo log, anomaly badges for unusually high amounts, CSV/JSON export
- **Budget tracking** — configurable monthly budget rules (effective-from dates), per-category spending limits with progress bars, configurable period start day (default: 7th of month)
- **Statistics** — multi-year trend chart, year-over-year grouped bars, category stacked chart, top/worst months, shopping interval stats for groceries and fuel
- **Receipt scanning** — phone camera → OCR (Tesseract) → pre-filled confirm form, all over the local network via QR code
- **Receipt sample database** — upload reference receipts per store to train keyword-based OCR parsing
- **Store aliases** — map raw stored names to a single canonical display name (e.g. "REWE Markt" → "Rewe") without touching the underlying data
- **Auto-categorisation** — store names matched against a keyword map; category can be overridden per entry or in bulk
- **Undo** — last 5 edits and bulk category changes are reversible from the expenses page
- **CSV / JSON export** — respects active filters; downloadable directly from the expenses page
- **DE / EN language toggle**
- **Responsive UI** — usable on mobile on the same network

## Tech Stack

| Layer    | Technology                                        |
|----------|---------------------------------------------------|
| Backend  | Python 3.11+, Flask, Flask-SQLAlchemy             |
| Database | SQLite (`data/expenses.db`)                       |
| Frontend | Bootstrap 5.3 dark theme, Chart.js, FontAwesome   |
| OCR      | pytesseract + Pillow (Tesseract binary required)  |
| QR codes | `qrcode` library (server-side PNG)                |

## Setup

```bash
# Create and activate virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux / macOS

# Install dependencies
pip install -r requirements.txt

# Run the app
python run.py
```

App runs at `http://localhost:5000`.  
Mobile access on the same network: `http://<your-local-ip>:5000`

## Importing historical CSV data

Place your CSV at `expenses_raw.csv` in the project root (format below), then visit `/import` and click **Import now**.

Expected CSV columns:

| Column     | Format                        |
|------------|-------------------------------|
| date       | `DD/MM/YYYY`                  |
| store      | Store name (e.g. `Aldi`)      |
| amount     | Euro amount (comma or period) |
| store_detail | Optional detail field       |

## Receipt Scanning

1. Open **Add expense** in the web UI — a QR code appears on the right.
2. Scan it with your phone (phone and PC must be on the same Wi-Fi).
3. Take a photo of the receipt on the mobile scan page.
4. If OCR is configured, store, amount, and date are extracted automatically.
5. Confirm or correct the pre-filled form and save.

To improve OCR accuracy for a specific store, upload reference receipts at **Receipt DB** in the nav. The app learns keyword positions from the samples.

### Enabling OCR (optional)

OCR requires the **Tesseract** binary in addition to the Python package:

| OS      | Installation |
|---------|-------------|
| Windows | Download from [UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki), install, add to `PATH` |
| Linux   | `sudo apt install tesseract-ocr tesseract-ocr-deu` |
| macOS   | `brew install tesseract tesseract-lang` |

Without Tesseract, scanning still works — the form opens pre-filled with whatever could be parsed and you correct it manually.

Tesseract path can be overridden in `config.py`:
```python
TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"  # or None to use PATH
```

## Budget Configuration

### Monthly budget rules

Managed at **Settings → Budget Rules**. Each rule has an *effective from* date and a monthly limit in €. The most recent rule before the start of a given period applies.

Default rules (seeded on first run, editable in the UI afterwards):

| Effective from | Monthly budget |
|----------------|---------------|
| 01.01.2000     | €300           |
| 30.05.2023     | €400           |

### Period start day

By default, budget periods start on the **7th of each month** (so a receipt dated April 2nd belongs to the March period). Change this in `config.py`:

```python
PERIOD_START_DAY = 7   # set to 1 for standard calendar months
```

### Per-category limits

Set monthly spending caps per category at **Settings → Category Limits**. The dashboard shows a progress-bar row for all limited categories, turning orange at 80% and red when exceeded.

### Store aliases

Map raw stored names to canonical display names at **Settings → Store Aliases**. Aliases are applied across the expense list, stats, filter dropdowns, and anomaly detection — the underlying data is unchanged.

## Anomaly Detection

Any expense in the list where the amount exceeds `mean + 2σ` for that store (minimum 5 historical data points) receives a yellow **!** badge. Useful for catching data entry errors.

## Export

On the Expenses page, use the **Export** button to download the current filtered view as CSV or JSON. The export respects all active filters (year, month, category, store, search).

## Project Structure

```
SpendScope/
├── run.py                      # Entry point
├── config.py                   # App config (DB path, Tesseract, budget seed, period start day)
├── requirements.txt
├── expenses_raw.csv            # Original historical data (not committed)
├── data/
│   ├── expenses.db             # SQLite database (auto-created, not committed)
│   └── receipt_samples/        # Uploaded receipt images (not committed)
└── app/
    ├── __init__.py             # App factory, DB init, context processor
    ├── models.py               # Expense, BudgetPeriod, ChangeLog, CategoryBudget,
    │                           #   StoreAlias, ReceiptSample, StoreProfile
    ├── parser.py               # CSV import, auto-categorisation, category map
    ├── stats.py                # All analytics, period helpers, anomaly detection
    ├── translations.py         # DE / EN strings + t() helper
    └── routes/
        ├── main.py             # Dashboard, expenses list, add/edit/delete, export, undo
        ├── api.py              # JSON API endpoints for Chart.js
        ├── scan.py             # QR scan, OCR pipeline, receipt sample DB
        └── settings.py         # Budget rules, category limits, store aliases
```

## Store Categories

| Category     | Matched stores                                                     |
|--------------|--------------------------------------------------------------------|
| Lebensmittel | Aldi, Lidl, Rewe, Penny, Kaufland, Edeka, Marktkauf, Combi, Netto, Pollmeier |
| Tanken       | Tanken                                                             |
| Drogerie     | Rossmann, DM, Müller, Bravo, Rituals                               |
| Einrichtung  | Ikea, Jysk                                                         |
| Ausgehen     | Restaurants, fast food, H2O, entertainment                         |
| Online       | Amazon                                                             |
| Baumarkt     | OBI, Toom, WEZ                                                     |
| Auto         | Waschstraße, Parken                                                |
| Sonstiges    | Everything else                                                    |

Categories and keyword mappings are defined in `app/parser.py` (`STORE_CATEGORY_MAP`).

## License

MIT — see [LICENSE](LICENSE).
