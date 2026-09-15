"""SpendScope application factory."""
import logging
import os
import sqlite3
from contextlib import closing
from datetime import date, datetime

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

    db.init_app(app)
    # Batch mode lets Alembic emulate ALTER TABLE on SQLite.
    migrate.init_app(app, db, directory=MIGRATIONS_DIR, render_as_batch=True)

    # Imported here because the route modules import ``db`` from this package.
    from app import stats
    from app.parser import CATEGORY_COLORS
    from app.routes.api import api
    from app.routes.main import main
    from app.routes.scan import scan
    from app.routes.settings import settings
    from app.translations import TRANSLATIONS, format_eur, get_lang, t

    app.register_blueprint(main)
    app.register_blueprint(api, url_prefix="/api")
    app.register_blueprint(settings, url_prefix="/settings")
    app.register_blueprint(scan, url_prefix="/scan")

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

    with app.app_context():
        os.makedirs(DATA_DIR, exist_ok=True)
        if _upgrade_database():
            _seed_budget_periods(config_class)

    return app


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

    if tables - {"alembic_version"}:
        _backup_sqlite_database(reason=f"before-{head}")
    if current is None and "expenses" in tables:
        baseline = script.get_base()
        logger.info("Existing database without migration history: stamping %s", baseline)
        stamp(revision=baseline)
    upgrade()
    return True


def _backup_sqlite_database(reason: str) -> None:
    """Copy the SQLite database to ``<db dir>/backups/`` using SQLite's backup API."""
    url = db.engine.url
    source = url.database
    if url.get_backend_name() != "sqlite" or not source or not os.path.isfile(source):
        return
    backup_dir = os.path.join(os.path.dirname(source), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(source))[0]
    target = os.path.join(backup_dir, f"{stem}-{datetime.now():%Y%m%d-%H%M%S}-{reason}.db")
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
    logger.info("Database backed up to %s", target)


def _seed_budget_periods(config_class: type[Config]) -> None:
    """Populate the BudgetPeriod table from ``BUDGET_HISTORY`` if it is empty."""
    from app.models import BudgetPeriod

    if BudgetPeriod.query.count() == 0:
        for effective_from, budget in config_class.BUDGET_HISTORY:
            db.session.add(BudgetPeriod(effective_from=effective_from, monthly_budget=budget))
        db.session.commit()
