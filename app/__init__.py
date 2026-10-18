"""SpendScope application factory."""
import logging
import os
import secrets
from datetime import date

import sqlalchemy as sa
from flask import Flask
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

from config import DATA_DIR, MIGRATIONS_DIR, Config

db = SQLAlchemy()
migrate = Migrate()
logger = logging.getLogger(__name__)


def create_app(config_class: type[Config] = Config) -> Flask:
    """Create and configure the Flask application.

    Args:
        config_class: Configuration object; tests pass a subclass of ``Config``
            pointing at a temporary database.

    Returns:
        The configured application with all blueprints registered, the
        database schema migrated to the latest revision and the budget rules
        seeded.
    """
    app = Flask(__name__)
    app.config.from_object(config_class)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = _load_secret_key(app.config.get("TESTING", False))

    db.init_app(app)
    # Batch mode lets Alembic emulate ALTER TABLE on SQLite.
    migrate.init_app(app, db, directory=MIGRATIONS_DIR, render_as_batch=True)

    # Imported here because the route modules import ``db`` from this package.
    from app import errors, security, stats
    from app.parser import CATEGORY_COLORS
    from app.routes.api import api
    from app.routes.main import main
    from app.routes.scan import scan
    from app.routes.settings import settings
    from app.translations import TRANSLATIONS, format_eur, format_pct, get_lang, t

    app.register_blueprint(main)
    app.register_blueprint(api, url_prefix="/api")
    app.register_blueprint(settings, url_prefix="/settings")
    app.register_blueprint(scan, url_prefix="/scan")
    security.init_app(app)
    errors.init_app(app)

    @app.context_processor
    def inject_globals() -> dict:
        """Expose translation helpers and shared lookups to every template."""
        lang = get_lang()
        td = TRANSLATIONS.get(lang, TRANSLATIONS["de"])
        return {
            "t": t,
            "lang": lang,
            "month_names": td["months"],
            "month_names_short": td["months_short"],
            "today": date.today(),
            "alias_map": stats.get_alias_map(),
            "category_colors": CATEGORY_COLORS,
        }

    # Locale-aware currency formatting: {{ amount|eur }} or {{ amount|eur(0) }}.
    app.add_template_filter(format_eur, name="eur")
    app.add_template_filter(format_pct, name="pct")

    with app.app_context():
        os.makedirs(DATA_DIR, exist_ok=True)
        if _upgrade_database():
            _seed_budget_periods(config_class)

    return app


def _load_secret_key(testing: bool) -> str:
    """Session signing key: kept in ``data/secret_key``, created on first start.

    Tests get a throwaway key so they never write into ``data/``.
    """
    if testing:
        return secrets.token_hex(32)
    path = os.path.join(DATA_DIR, "secret_key")
    try:
        with open(path, encoding="ascii") as fh:
            key = fh.read().strip()
        if key:
            return key
    except FileNotFoundError:
        pass
    os.makedirs(DATA_DIR, exist_ok=True)
    key = secrets.token_hex(32)
    with open(path, "w", encoding="ascii") as fh:
        fh.write(key)
    logger.info("Generated a new session key in %s", path)
    return key


def _upgrade_database() -> bool:
    """Bring the database schema to the latest Alembic revision.

    Databases created before migrations were introduced (tables present but
    no ``alembic_version``) already match the first revision and are stamped
    with it instead of being re-created. Whenever an existing database with
    tables is about to change, a copy is written to ``backups/`` first.

    Returns:
        ``False`` only while bootstrapping, when no migration exists yet.
    """
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from flask_migrate import stamp, upgrade

    from app import backup

    if not os.path.isdir(MIGRATIONS_DIR):
        return False
    script = ScriptDirectory.from_config(migrate.get_config())
    head = script.get_current_head()
    if head is None:
        return False

    with db.engine.connect() as conn:
        tables = set(sa.inspect(conn).get_table_names())
        current = MigrationContext.configure(conn).get_current_revision()

    if current == head:
        return True

    if tables - {"alembic_version"} and backup.database_path():
        # The settings table may not exist yet, so always use the default folder.
        backup.create_backup(f"{backup.MIGRATION_PREFIX}{head}", backup.default_dir())
    if current is None and "expenses" in tables:
        baseline = script.get_base()
        logger.info("Existing database without migration history: stamping %s", baseline)
        stamp(revision=baseline)
    upgrade()
    return True


def _seed_budget_periods(config_class: type[Config]) -> None:
    """Populate the BudgetPeriod table from ``BUDGET_HISTORY`` if it is empty."""
    from app.models import BudgetPeriod

    if BudgetPeriod.query.count() == 0:
        for effective_from, budget in config_class.BUDGET_HISTORY:
            db.session.add(BudgetPeriod(effective_from=effective_from, monthly_budget=budget))
        db.session.commit()
