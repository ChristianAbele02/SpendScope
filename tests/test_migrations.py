"""Schema migrations: models and migrations agree, legacy databases are adopted safely."""
from datetime import date

import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app import create_app, db, migrate
from config import Config

# Tables added by migrations after the 0001 baseline; a pre-migration database
# created by the old db.create_all() startup code never had them.
POST_BASELINE_TABLES = {"app_settings"}


def _create_legacy_schema(engine) -> None:
    tables = [t for name, t in db.metadata.tables.items() if name not in POST_BASELINE_TABLES]
    db.metadata.create_all(engine, tables=tables)


def _config_for(tmp_path, db_file):
    class TestConfig(Config):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + db_file.as_posix()
        RECEIPT_SAMPLES_DIR = str(tmp_path / "receipt_samples")
        TESTING = True

    return TestConfig


def test_migrations_match_models(ctx):
    """Fails when a model changes without a new migration (`flask db migrate`)."""
    with db.engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), db.metadata)
    assert diff == []


def test_legacy_database_is_stamped_backed_up_and_keeps_data(tmp_path):
    db_file = tmp_path / "legacy.db"

    # Simulate a database created by the old db.create_all() startup code.
    engine = sa.create_engine("sqlite:///" + db_file.as_posix())
    _create_legacy_schema(engine)
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO expenses (date, store, amount, category) "
            "VALUES ('2024-01-02', 'Aldi', 9.99, 'Lebensmittel')"
        ))
    engine.dispose()

    app = create_app(_config_for(tmp_path, db_file))
    with app.app_context():
        with db.engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
            rows = conn.execute(sa.text("SELECT store, amount, date FROM expenses")).all()
        head = ScriptDirectory.from_config(migrate.get_config()).get_current_head()
        db.engine.dispose()

    assert current == head
    assert rows == [("Aldi", 9.99, str(date(2024, 1, 2)))]
    backups = list((tmp_path / "backups").glob("legacy-*.db"))
    assert len(backups) == 1
