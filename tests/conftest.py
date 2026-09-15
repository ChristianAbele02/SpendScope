import pytest

from app import create_app
from config import Config


@pytest.fixture
def app(tmp_path):
    """A fresh application isolated from the real data directory.

    The database, receipt samples and CSV path all point into ``tmp_path``,
    so tests can never read or modify data/expenses.db or expenses_raw.csv.
    """

    class TestConfig(Config):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + (tmp_path / "test.db").as_posix()
        RECEIPT_SAMPLES_DIR = str(tmp_path / "receipt_samples")
        CSV_PATH = str(tmp_path / "missing.csv")
        TESTING = True
        PERIOD_START_DAY = 1  # calendar months unless a test overrides it

    application = create_app(TestConfig)
    yield application

    from app import db

    with application.app_context():
        db.engine.dispose()  # release the SQLite file handle (Windows)


@pytest.fixture
def ctx(app):
    """Application context so DB-backed helpers and config reads work."""
    with app.app_context():
        yield app
