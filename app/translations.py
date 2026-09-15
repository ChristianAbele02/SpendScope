"""German / English UI strings.

Every key must exist in both dictionaries (enforced by tests/test_translations.py).
Placeholders are positional ``{}`` markers filled by ``t(key, *args)``.
"""
from flask import has_request_context, session

TRANSLATIONS: dict[str, dict] = {
    "de": {
        # Month names
        "months": ["", "Januar", "Februar", "März", "April", "Mai", "Juni",
                   "Juli", "August", "September", "Oktober", "November", "Dezember"],
        "months_short": ["", "Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
                         "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"],

        # Nav
        "nav_dashboard":   "Dashboard",
        "nav_expenses":    "Ausgaben",
        "nav_add":         "Hinzufügen",
        "nav_statistics":  "Statistiken",
        "nav_import":      "Import",

        # Common
        "budget":       "Budget",
        "date":         "Datum",
        "store":        "Laden",
        "category":     "Kategorie",
        "amount":       "Betrag",
        "all":          "Alle",
        "year":         "Jahr",
        "month":        "Monat",
        "save":         "Speichern",
        "cancel":       "Abbrechen",
        "total":        "Gesamt",
        "actions":      "Aktionen",
        "note":         "Notiz",
        "new_expense":  "Neue Ausgabe",
        "search":       "Suche",
        "edit":         "Bearbeiten",
        "delete":       "Löschen",
        "over_budget_short": "über Budget",
        "store_ph":     "z.B. Aldi, Lidl, Tanken ...",

        # Dashboard KPIs
        "spent":             "Ausgegeben",
        "remaining":         "Verbleibend",
        "transactions":      "Transaktionen",
        "days":              "Tage",
        "pct_of_budget":     "% des Budgets",
        "avg_per_purchase":  "Ø pro Einkauf",
        "avg_per_day":       "/ Tag",
        "past_month":        "Vergangener Monat",
        "current_badge":     "Aktuell",

        # Dashboard sections
        "monthly_spending":   "Monatliche Ausgaben",
        "chart_spending":     "Ausgaben",
        "all_years":          "Alle Jahre",
        "categories":         "Kategorien",
        "top_stores":         "Top Läden",
        "last_transactions":  "Letzte Transaktionen",
        "no_data_month":      "Keine Daten für diesen Monat",
        "no_data_warning":    "Noch keine Daten vorhanden.",
        "import_csv_link":    "CSV importieren",

        # Prediction card
        "forecast_title":      "Prognose bis Monatsende",
        "days_remaining_lbl":  "Tage verbleibend",
        "budget_exceeded":     "Budget überschritten",
        "on_budget":           "Im Budget",
        "predicted_remaining": "Erwartetes Restbudget",
        "predicted_total":     "Erwartete Gesamtausgaben",
        "groceries":           "Lebensmittel",
        "fuel":                "Tanken",
        "purchases":           "Einkäufe",
        "stops":               "Stopps",
        "spent_so_far":        "Bisher",
        "forecast":            "Prognose",
        "every_n_days":        "alle {}d",
        "last_n_days_ago":     "zuletzt vor {}d",
        "avg_rate_per_day":    "Ø €{} / Tag",
        "prob_over":           "Überschreitungsrisiko",
        "ci_range":            "90%-Intervall",

        # Statistics page
        "stats_title":          "Erweiterte Statistiken",
        "total_spent":          "Gesamt ausgegeben",
        "months_tracked":       "Monate erfasst",
        "avg_per_month":        "Ø pro Monat",
        "avg_per_trip":         "Ø pro Einkauf",
        "on_budget_pct":        "Im Budget",
        "of_months":            "der Monate",
        "yearly_comparison":    "Jahresvergleich (monatlich)",
        "categories_over_time": "Kategorien über Zeit",
        "shopping_behavior":    "Einkaufsverhalten (letzte 12 Monate)",
        "avg_interval":         "Ø Intervall",
        "avg_per_trip_short":   "Ø pro Einkauf",
        "last_short":           "Zuletzt",
        "n_days_ago":           "vor {}d",
        "not_enough_data":      "Nicht genug Daten",
        "expensive_months":     "Teuerste Monate",
        "cheapest_months":      "Sparsamste Monate",

        # Expenses list
        "expenses_title":    "Ausgaben",
        "search_ph":         "Laden, Notiz...",
        "reset_filters":     "Filter zurücksetzen",
        "entries_found":     "Einträge gefunden",
        "no_expenses":       "Keine Ausgaben gefunden.",
        "no_date":           "kein Datum",
        "really_delete":     "Wirklich löschen?",

        # Add expense form
        "add_title":        "Neue Ausgabe",
        "store_required":   "Laden",
        "detail_optional":  'Detail (optional, z.B. bei "Sonstige")',
        "detail_ph":        "z.B. Rossmann, Parken, Waschstraße ...",
        "amount_required":  "Betrag (€)",
        "negative_refund":  "Negativer Betrag für Erstattungen.",
        "note_optional":    "Notiz (optional)",
        "note_ph":          "Optionale Anmerkung",

        # Settings: budget
        "nav_settings":         "Einstellungen",
        "settings_budget":      "Budget-Regeln",
        "budget_periods":       "Budget-Zeiträume",
        "effective_from":       "Gültig ab",
        "monthly_budget_col":   "Monatsbudget",
        "budget_last_entry":    "Letzter Eintrag kann nicht gelöscht werden",
        "budget_rule_hint":     "Für jeden Budget-Zeitraum gilt die neueste Regel, deren Datum am oder vor dem Beginn des Zeitraums liegt.",
        "budget_add_new":       "Neuen Zeitraum hinzufügen",
        "budget_from_hint":     "Gilt ab dem ersten Budget-Zeitraum, der an oder nach diesem Datum beginnt.",
        "budget_note_ph":       "z.B. Gehaltserhöhung",
        "optional":             "optional",

        # Scan / QR
        "scan_title":           "Kassenbon scannen",
        "scan_qr_title":        "Mit Handy scannen",
        "scan_qr_hint":         "Öffne diese Seite auf deinem Handy, um direkt einen Kassenbon zu scannen.",
        "scan_qr_hint_short":   "Scanne mit dem Handy, um einen Kassenbon einzuscannen.",
        "scan_qr_url":          "URL:",
        "scan_qr_network_hint": "Handy und PC müssen im selben WLAN sein.",
        "scan_open_mobile":     "Auf Handy öffnen",
        "scan_fullscreen_qr":   "QR-Code vergrößern",
        "scan_manual_entry":    "Manuell eingeben",
        "scan_tap_hint":        "Tippe auf den Button und fotografiere den Kassenbon.",
        "scan_take_photo":      "Foto aufnehmen",
        "scan_retake":          "Neu aufnehmen",
        "scan_extract":         "Daten extrahieren",
        "scan_processing":      "Wird verarbeitet...",
        "scan_confirm_data":    "Daten bestätigen",
        "scan_auto_filled":     "Automatisch ausgefüllt",
        "scan_ocr_unavailable": "OCR nicht verfügbar, bitte manuell ausfüllen. Für automatische Erkennung installiere",

        # Import page
        "import_title":       "CSV Import",
        "import_desc":        "Importiert Daten aus <code>expenses_raw.csv</code> im Projektverzeichnis. Die Datei selbst wird nie verändert.",
        "import_success":     "Import erfolgreich!",
        "imported":           "Importiert",
        "skipped":            "Übersprungen",
        "duplicates":         "Duplikate übersprungen",
        "to_dashboard":       "Zum Dashboard",
        "error":              "Fehler",
        "import_append_btn":  "Neue Einträge hinzufügen",
        "import_append_hint": "Empfohlen. Fügt nur neue Zeilen hinzu; vorhandene Einträge (gleiches Datum, Laden und Betrag) werden übersprungen. Manuell oder per Scan erfasste Daten bleiben erhalten.",
        "import_replace_label": "Stattdessen alles ersetzen (zerstörerisch)",
        "import_replace_confirm": "Ich verstehe, dass dabei alle bestehenden Einträge gelöscht werden.",
        "import_replace_btn": "Alles löschen und neu importieren",
        "csv_format":         "CSV-Format",
        "expected_format":    "Erwartet wird folgendes Format:",
        "date_format_hint":   "Datum: <code>DD/MM/YYYY</code>",
        "store_name_hint":    "Laden: Storename (z.B. Aldi, Lidl, Sonstige)",
        "amount_hint":        "Ausgaben: Betrag in Euro (Komma oder Punkt)",
        "detail_hint":        'Detail: Optional, z.B. bei "Sonstige"',

        # Edit expense
        "edit_title":          "Eintrag bearbeiten",
        "edit_auto_category":  "Automatisch bestimmen",
        "edit_category_hint":  "Leer lassen = Kategorie wird aus dem Ladennamen abgeleitet.",
        "bulk_apply_title":    "Kategorie auf alle anwenden?",
        "bulk_apply_desc":     'Soll die Kategorie "{}" für alle anderen {}-Einträge, die noch als "{}" kategorisiert sind ({} weitere), ebenfalls gesetzt werden?',
        "bulk_yes":            "Ja, alle aktualisieren",
        "bulk_no":             "Nein, nur dieser",
        "undo":                "Rückgängig",

        # Settings nav sub-items
        "nav_settings_budget":  "Budget-Regeln",
        "nav_settings_limits":  "Kategorie-Limits",
        "nav_settings_aliases": "Laden-Aliase",

        # Export
        "export_csv":           "CSV exportieren",
        "export_json":          "JSON exportieren",
        "export_btn":           "Exportieren",

        # Year-over-year
        "yoy_label":            "Vorjahr (gleicher Monat)",
        "yoy_less":             "weniger als letztes Jahr",
        "yoy_more":             "mehr als letztes Jahr",
        "yoy_no_data":          "Kein Vorjahreswert",

        # Category limits
        "cat_limit_title":      "Kategorie-Budgets",
        "cat_limit_add":        "Limit hinzufügen",
        "cat_limit_col":        "Monatslimit",
        "cat_limit_hint":       "Monatliche Ausgabenobergrenze pro Kategorie.",
        "cat_limit_no_limits":  "Keine Limits definiert.",
        "cat_limit_status":     "Kategorie-Budget-Status",

        # Store aliases
        "alias_title":          "Laden-Aliase",
        "alias_add":            "Alias hinzufügen",
        "alias_raw":            "Gespeicherter Name",
        "alias_canonical":      "Angezeigter Name",
        "alias_hint":           "Ordne gespeicherte Rohdaten-Namen einem einheitlichen Anzeigenamen zu (z.B. 'REWE Markt' → 'Rewe').",
        "alias_no_aliases":     "Keine Aliase definiert.",
        "alias_raw_ph":         "z.B. REWE Markt",
        "alias_canonical_ph":   "z.B. Rewe",

        # Anomaly
        "anomaly_hint":         "Ungewöhnlich hoch für diesen Laden",

        # Receipt sample database
        "nav_samples":         "Belegdatenbank",
        "samples_title":       "Belegdatenbank",
        "samples_upload":      "Beleg hochladen",
        "samples_upload_btn":  "Hochladen & analysieren",
        "samples_image":       "Bild",
        "samples_profiles":    "Gelernte Regeln pro Laden",
        "samples_count":       "Belege",
        "samples_keyword":     "Keyword",
        "samples_next_line":   "Betrag nächste Zeile",
        "samples_reanalyze":   "Neu analysieren",
        "samples_all":         "Alle Belege",
        "samples_no_samples":  "Noch keine Belege hochgeladen.",
        "samples_no_profiles": "Noch keine Profile gelernt. Lade Belege hoch.",
        "samples_no_amount":   "Betrag nicht erkannt",
        "samples_show_ocr":    "OCR-Text anzeigen",
        "samples_info":        "Jeder hochgeladene Beleg wird per OCR analysiert. Das System lernt daraus, welche Keywords (z.B. <code>SUMME</code>, <code>GESAMT</code>) für diesen Laden den Gesamtbetrag markieren, und verbessert damit zukünftige Scans.",
        "full_size":           "Vollbild",

        # Flash messages
        "flash_store_required":   "Laden ist ein Pflichtfeld.",
        "flash_invalid_amount":   "Ungültiger Betrag.",
        "flash_invalid_date":     "Ungültiges Datum.",
        "flash_invalid_request":  "Ungültige Anfrage.",
        "flash_expense_added":    "Ausgabe von {} bei {} gespeichert.",
        "flash_entry_updated":    "Eintrag aktualisiert.",
        "flash_bulk_updated":     "{} Einträge von '{}' aktualisiert: {} → {}.",
        "flash_already_undone":   "Bereits rückgängig gemacht.",
        "flash_entry_missing":    "Eintrag existiert nicht mehr.",
        "flash_undo_edit":        "Rückgängig: Eintrag wiederhergestellt.",
        "flash_undo_bulk":        "Rückgängig: {} Einträge zurück auf '{}'.",
        "flash_undo_blocked":     "Bitte zuerst die neueren Änderungen an diesem Eintrag rückgängig machen.",
        "flash_expense_deleted":  "Ausgabe gelöscht.",
        "flash_sample_required":  "Laden und Bild sind Pflichtfelder.",
        "flash_sample_bad_type":  "Nur Bilddateien (JPG, PNG, WebP, HEIC, BMP, TIFF) sind erlaubt.",
        "flash_sample_saved":     "Beleg gespeichert und analysiert, Betrag: {}.",
        "flash_sample_ocr_failed": "Beleg gespeichert, aber OCR fehlgeschlagen: {}",
        "flash_sample_deleted":   "Beleg gelöscht.",
        "flash_profile_rebuilt":  "Profil für '{}' aus {} Belegen neu erstellt.",
        "flash_budget_exists":    "Für dieses Datum existiert bereits ein Eintrag.",
        "flash_budget_saved":     "Budget {}/Monat ab {} gespeichert.",
        "flash_budget_keep_one":  "Mindestens ein Budget-Eintrag muss vorhanden bleiben.",
        "flash_budget_deleted":   "Budget-Eintrag gelöscht.",
        "flash_no_category":      "Keine gültige Kategorie angegeben.",
        "flash_limit_updated":    "Limit für {} auf {} aktualisiert.",
        "flash_limit_set":        "Limit {}/Monat für {} gesetzt.",
        "flash_limit_deleted":    "Kategorie-Limit gelöscht.",
        "flash_alias_required":   "Alias und Zielname müssen ausgefüllt sein.",
        "flash_alias_identical":  "Alias und Zielname dürfen nicht identisch sein.",
        "flash_alias_updated":    "Alias '{}' → '{}' aktualisiert.",
        "flash_alias_saved":      "Alias '{}' → '{}' gespeichert.",
        "flash_alias_deleted":    "Alias gelöscht.",
    },

    "en": {
        # Month names
        "months": ["", "January", "February", "March", "April", "May", "June",
                   "July", "August", "September", "October", "November", "December"],
        "months_short": ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],

        # Nav
        "nav_dashboard":   "Dashboard",
        "nav_expenses":    "Expenses",
        "nav_add":         "Add",
        "nav_statistics":  "Statistics",
        "nav_import":      "Import",

        # Common
        "budget":       "Budget",
        "date":         "Date",
        "store":        "Store",
        "category":     "Category",
        "amount":       "Amount",
        "all":          "All",
        "year":         "Year",
        "month":        "Month",
        "save":         "Save",
        "cancel":       "Cancel",
        "total":        "Total",
        "actions":      "Actions",
        "note":         "Note",
        "new_expense":  "New Expense",
        "search":       "Search",
        "edit":         "Edit",
        "delete":       "Delete",
        "over_budget_short": "over budget",
        "store_ph":     "e.g. Aldi, Lidl, Tanken ...",

        # Dashboard KPIs
        "spent":             "Spent",
        "remaining":         "Remaining",
        "transactions":      "Transactions",
        "days":              "Days",
        "pct_of_budget":     "% of budget",
        "avg_per_purchase":  "Avg per purchase",
        "avg_per_day":       "/ day",
        "past_month":        "Past month",
        "current_badge":     "Current",

        # Dashboard sections
        "monthly_spending":   "Monthly Spending",
        "chart_spending":     "Spending",
        "all_years":          "All years",
        "categories":         "Categories",
        "top_stores":         "Top Stores",
        "last_transactions":  "Recent Transactions",
        "no_data_month":      "No data for this month",
        "no_data_warning":    "No data yet.",
        "import_csv_link":    "Import CSV",

        # Prediction card
        "forecast_title":      "Forecast to end of month",
        "days_remaining_lbl":  "days remaining",
        "budget_exceeded":     "Budget exceeded",
        "on_budget":           "On Budget",
        "predicted_remaining": "Expected remaining",
        "predicted_total":     "Expected total spend",
        "groceries":           "Groceries",
        "fuel":                "Fuel",
        "purchases":           "purchases",
        "stops":               "stops",
        "spent_so_far":        "So far",
        "forecast":            "Forecast",
        "every_n_days":        "every {}d",
        "last_n_days_ago":     "last {}d ago",
        "avg_rate_per_day":    "Avg €{} / day",
        "prob_over":           "Risk of overspend",
        "ci_range":            "90% interval",

        # Statistics page
        "stats_title":          "Advanced Statistics",
        "total_spent":          "Total spent",
        "months_tracked":       "Months tracked",
        "avg_per_month":        "Avg per month",
        "avg_per_trip":         "Avg per purchase",
        "on_budget_pct":        "On budget",
        "of_months":            "of months",
        "yearly_comparison":    "Year-over-Year (monthly)",
        "categories_over_time": "Categories over Time",
        "shopping_behavior":    "Shopping Behaviour (last 12 months)",
        "avg_interval":         "Avg interval",
        "avg_per_trip_short":   "Avg per trip",
        "last_short":           "Last",
        "n_days_ago":           "{}d ago",
        "not_enough_data":      "Not enough data",
        "expensive_months":     "Most expensive months",
        "cheapest_months":      "Most frugal months",

        # Expenses list
        "expenses_title":    "Expenses",
        "search_ph":         "Store, note...",
        "reset_filters":     "Reset filters",
        "entries_found":     "entries found",
        "no_expenses":       "No expenses found.",
        "no_date":           "no date",
        "really_delete":     "Really delete?",

        # Add expense form
        "add_title":        "New Expense",
        "store_required":   "Store",
        "detail_optional":  'Detail (optional, e.g. for "Sonstige")',
        "detail_ph":        "e.g. Rossmann, Parken, Waschstraße ...",
        "amount_required":  "Amount (€)",
        "negative_refund":  "Negative amount for refunds.",
        "note_optional":    "Note (optional)",
        "note_ph":          "Optional remark",

        # Settings: budget
        "nav_settings":         "Settings",
        "settings_budget":      "Budget Rules",
        "budget_periods":       "Budget Periods",
        "effective_from":       "Effective from",
        "monthly_budget_col":   "Monthly budget",
        "budget_last_entry":    "The last entry cannot be deleted",
        "budget_rule_hint":     "Each budget period uses the newest rule dated on or before the start of that period.",
        "budget_add_new":       "Add new period",
        "budget_from_hint":     "Applies from the first budget period starting on or after this date.",
        "budget_note_ph":       "e.g. Pay rise",
        "optional":             "optional",

        # Scan / QR
        "scan_title":           "Scan receipt",
        "scan_qr_title":        "Scan with phone",
        "scan_qr_hint":         "Open this page on your phone to scan a receipt directly.",
        "scan_qr_hint_short":   "Scan with your phone to capture a receipt.",
        "scan_qr_url":          "URL:",
        "scan_qr_network_hint": "Phone and PC must be on the same Wi-Fi network.",
        "scan_open_mobile":     "Open on phone",
        "scan_fullscreen_qr":   "Enlarge QR code",
        "scan_manual_entry":    "Enter manually",
        "scan_tap_hint":        "Tap the button and take a photo of the receipt.",
        "scan_take_photo":      "Take photo",
        "scan_retake":          "Retake",
        "scan_extract":         "Extract data",
        "scan_processing":      "Processing...",
        "scan_confirm_data":    "Confirm details",
        "scan_auto_filled":     "Auto-filled",
        "scan_ocr_unavailable": "OCR unavailable, please fill in manually. For automatic detection, install",

        # Import page
        "import_title":       "CSV Import",
        "import_desc":        "Imports data from <code>expenses_raw.csv</code> in the project directory. The file itself is never modified.",
        "import_success":     "Import successful!",
        "imported":           "Imported",
        "skipped":            "Skipped",
        "duplicates":         "Duplicates skipped",
        "to_dashboard":       "To Dashboard",
        "error":              "Error",
        "import_append_btn":  "Add new entries",
        "import_append_hint": "Recommended. Adds new rows only; entries already present (same date, store and amount) are skipped. Manually added or scanned data is preserved.",
        "import_replace_label": "Replace everything instead (destructive)",
        "import_replace_confirm": "I understand this deletes all existing entries.",
        "import_replace_btn": "Delete all and reimport",
        "csv_format":         "CSV Format",
        "expected_format":    "Expected format:",
        "date_format_hint":   "Date: <code>DD/MM/YYYY</code>",
        "store_name_hint":    "Store: store name (e.g. Aldi, Lidl, Sonstige)",
        "amount_hint":        "Expenses: amount in euros (comma or period)",
        "detail_hint":        'Detail: optional, e.g. for "Sonstige"',

        # Edit expense
        "edit_title":          "Edit entry",
        "edit_auto_category":  "Auto-detect",
        "edit_category_hint":  "Leave empty to derive the category from the store name.",
        "bulk_apply_title":    "Apply category to all matching?",
        "bulk_apply_desc":     "Apply category \"{}\" to all other {} entries still categorised as \"{}\" ({} more)?",
        "bulk_yes":            "Yes, update all",
        "bulk_no":             "No, just this one",
        "undo":                "Undo",

        # Settings nav sub-items
        "nav_settings_budget":  "Budget Rules",
        "nav_settings_limits":  "Category Limits",
        "nav_settings_aliases": "Store Aliases",

        # Export
        "export_csv":           "Export CSV",
        "export_json":          "Export JSON",
        "export_btn":           "Export",

        # Year-over-year
        "yoy_label":            "Prior year (same period)",
        "yoy_less":             "less than last year",
        "yoy_more":             "more than last year",
        "yoy_no_data":          "No prior-year data",

        # Category limits
        "cat_limit_title":      "Category Budgets",
        "cat_limit_add":        "Add limit",
        "cat_limit_col":        "Monthly limit",
        "cat_limit_hint":       "Monthly spending cap per category.",
        "cat_limit_no_limits":  "No limits defined.",
        "cat_limit_status":     "Category Budget Status",

        # Store aliases
        "alias_title":          "Store Aliases",
        "alias_add":            "Add alias",
        "alias_raw":            "Stored name",
        "alias_canonical":      "Display name",
        "alias_hint":           "Map raw stored names to a single canonical display name (e.g. 'REWE Markt' → 'Rewe').",
        "alias_no_aliases":     "No aliases defined.",
        "alias_raw_ph":         "e.g. REWE Markt",
        "alias_canonical_ph":   "e.g. Rewe",

        # Anomaly
        "anomaly_hint":         "Unusually high for this store",

        # Receipt sample database
        "nav_samples":         "Receipt DB",
        "samples_title":       "Receipt Database",
        "samples_upload":      "Upload Receipt",
        "samples_upload_btn":  "Upload & analyse",
        "samples_image":       "Image",
        "samples_profiles":    "Learned rules per store",
        "samples_count":       "Samples",
        "samples_keyword":     "Keyword",
        "samples_next_line":   "Amount on next line",
        "samples_reanalyze":   "Re-analyse",
        "samples_all":         "All receipts",
        "samples_no_samples":  "No receipts uploaded yet.",
        "samples_no_profiles": "No profiles learned yet. Upload some receipts.",
        "samples_no_amount":   "Amount not detected",
        "samples_show_ocr":    "Show OCR text",
        "samples_info":        "Each uploaded receipt is analysed by OCR. The system learns which keyword (e.g. <code>SUMME</code>, <code>GESAMT</code>) marks the total for that store and uses it to improve future scans.",
        "full_size":           "Full size",

        # Flash messages
        "flash_store_required":   "Store is required.",
        "flash_invalid_amount":   "Invalid amount.",
        "flash_invalid_date":     "Invalid date.",
        "flash_invalid_request":  "Invalid request.",
        "flash_expense_added":    "Expense of {} at {} saved.",
        "flash_entry_updated":    "Entry updated.",
        "flash_bulk_updated":     "Updated {} '{}' entries: {} → {}.",
        "flash_already_undone":   "Already undone.",
        "flash_entry_missing":    "Entry no longer exists.",
        "flash_undo_edit":        "Undone: entry restored.",
        "flash_undo_bulk":        "Undone: {} entries reverted to '{}'.",
        "flash_undo_blocked":     "Undo the newer changes to this entry first.",
        "flash_expense_deleted":  "Expense deleted.",
        "flash_sample_required":  "Store and image are required.",
        "flash_sample_bad_type":  "Only image files (JPG, PNG, WebP, HEIC, BMP, TIFF) are allowed.",
        "flash_sample_saved":     "Sample saved and analysed, amount: {}.",
        "flash_sample_ocr_failed": "Sample saved, but OCR failed: {}",
        "flash_sample_deleted":   "Sample deleted.",
        "flash_profile_rebuilt":  "Profile for '{}' rebuilt from {} samples.",
        "flash_budget_exists":    "An entry for this date already exists.",
        "flash_budget_saved":     "Budget {}/month from {} saved.",
        "flash_budget_keep_one":  "At least one budget entry must remain.",
        "flash_budget_deleted":   "Budget entry deleted.",
        "flash_no_category":      "No valid category given.",
        "flash_limit_updated":    "Limit for {} updated to {}.",
        "flash_limit_set":        "Limit {}/month set for {}.",
        "flash_limit_deleted":    "Category limit deleted.",
        "flash_alias_required":   "Alias and display name are required.",
        "flash_alias_identical":  "Alias and display name must differ.",
        "flash_alias_updated":    "Alias '{}' → '{}' updated.",
        "flash_alias_saved":      "Alias '{}' → '{}' saved.",
        "flash_alias_deleted":    "Alias deleted.",
    },
}

SUPPORTED_LANGS = ("de", "en")
DEFAULT_LANG = "de"


def get_lang() -> str:
    """Current UI language from the session (German outside a request)."""
    if not has_request_context():
        return DEFAULT_LANG
    return session.get("lang", DEFAULT_LANG)


def format_eur(value, decimals: int = 2) -> str:
    """Format a number as euros with locale-appropriate grouping.

    German (de): ``1.234,56 €`` (dot thousands, comma decimal, trailing sign).
    English (en): ``€1,234.56`` (comma thousands, dot decimal, leading sign).
    A leading minus is preserved for refunds. Non-numeric input is returned
    unchanged so the filter never raises inside a template.
    """
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    sign = "-" if num < 0 else ""
    grouped = f"{abs(num):,.{decimals}f}"  # 1,234.56, US grouping baseline
    if get_lang() == "de":
        # Swap separators to German convention via a placeholder.
        grouped = grouped.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
        return f"{sign}{grouped} €"
    return f"{sign}€{grouped}"


def t(key: str, *args) -> str:
    """Translate ``key`` for the current language and fill ``{}`` placeholders.

    Falls back to the German string, then to the key itself. Arguments are
    inserted in order; braces inside an argument (e.g. a store name) are left
    untouched.

    Example:
        ``t("every_n_days", 3)`` → ``"every 3d"``
    """
    td = TRANSLATIONS.get(get_lang(), TRANSLATIONS[DEFAULT_LANG])
    text: str = td.get(key) or TRANSLATIONS[DEFAULT_LANG].get(key, key)
    if not args:
        return text
    parts = text.split("{}")
    out = [parts[0]]
    for i, part in enumerate(parts[1:]):
        out.append(str(args[i]) if i < len(args) else "{}")
        out.append(part)
    return "".join(out)
