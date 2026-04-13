from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from config import Config

db = SQLAlchemy()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)

    from app.routes.main import main
    from app.routes.api import api
    from app.routes.settings import settings
    from app.routes.scan import scan

    app.register_blueprint(main)
    app.register_blueprint(api, url_prefix="/api")
    app.register_blueprint(settings, url_prefix="/settings")
    app.register_blueprint(scan, url_prefix="/scan")

    # Inject translation helpers and global helpers into every template
    @app.context_processor
    def inject_lang():
        from datetime import date
        from app.translations import t, get_lang, TRANSLATIONS
        from app import stats as s
        lang = get_lang()
        td = TRANSLATIONS.get(lang, TRANSLATIONS["de"])
        return {
            "t":                 t,
            "lang":              lang,
            "month_names":       td["months"],
            "month_names_short": td["months_short"],
            "today":             date.today(),
            "alias_map":         s.get_alias_map(),
        }

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
