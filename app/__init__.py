from flask import Flask
from flask_sqlalchemy import SQLAlchemy

from config import Config

db = SQLAlchemy()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)

    from app.routes.api import api
    from app.routes.main import main
    from app.routes.scan import scan
    from app.routes.settings import settings

    app.register_blueprint(main)
    app.register_blueprint(api, url_prefix="/api")
    app.register_blueprint(settings, url_prefix="/settings")
    app.register_blueprint(scan, url_prefix="/scan")

    # Inject translation helpers and global helpers into every template
    @app.context_processor
    def inject_lang():
        from datetime import date

        from app import stats as s
        from app.parser import CATEGORY_COLORS
        from app.translations import TRANSLATIONS, get_lang, t
        lang = get_lang()
        td = TRANSLATIONS.get(lang, TRANSLATIONS["de"])
        return {
            "t":                 t,
            "lang":              lang,
            "month_names":       td["months"],
            "month_names_short": td["months_short"],
            "today":             date.today(),
            "alias_map":         s.get_alias_map(),
            "category_colors":   CATEGORY_COLORS,
        }

    # Locale-aware currency formatting, available as a Jinja filter ({{ x|eur }}).
    from app.translations import format_eur
    app.add_template_filter(format_eur, name="eur")

    with app.app_context():
        import os
        os.makedirs(
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"),
            exist_ok=True,
        )
        db.create_all()
        _seed_budget_periods()

    return app


def _seed_budget_periods():
    """Populate BudgetPeriod table from config defaults if empty."""
    from app.models import BudgetPeriod
    from config import Config

    if BudgetPeriod.query.count() == 0:
        for effective_from, budget in Config.BUDGET_HISTORY:
            db.session.add(
                BudgetPeriod(effective_from=effective_from, monthly_budget=budget)
            )
        db.session.commit()
