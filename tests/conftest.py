import os
import tempfile

import pytest

from app import create_app
from config import Config


@pytest.fixture
def app():
    """A fresh application backed by a throwaway SQLite file per test."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    uri = "sqlite:///" + path.replace("\\", "/")

    class TestConfig(Config):
        SQLALCHEMY_DATABASE_URI = uri
        TESTING = True
        PERIOD_START_DAY = 1  # calendar months unless a test overrides it

    application = create_app(TestConfig)
    yield application

    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def ctx(app):
    """Application context so DB-backed helpers and config reads work."""
    with app.app_context():
        yield app
