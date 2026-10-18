"""Database backups: automatic (scheduled), manual, restore and their settings.

Backups are complete copies of the SQLite file made with SQLite's online
backup API, so they are consistent even while the app is writing. File names
encode when and why a copy was made::

    expenses-20261018-163000-auto.db
    expenses-20261018-170512-manual.db
    expenses-20261018-171500-before-restore.db
    expenses-20261018-090000-before-0002.db      (made before a schema upgrade)

Automatic backups run when the app starts and then from a background thread
(:func:`start_scheduler`) whenever the configured interval has passed. Only
automatic backups are pruned to the configured number; manual and safety
copies stay until the user deletes them.
"""
import logging
import os
import re
import sqlite3
import tempfile
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta

from flask import Flask

from app import db
from app.models import AppSetting
from config import APP_HOME

logger = logging.getLogger(__name__)

REASON_AUTO = "auto"
REASON_MANUAL = "manual"
REASON_RESTORE = "before-restore"
MIGRATION_PREFIX = "before-"

INTERVAL_CHOICES_HOURS = (6, 24, 72, 168)  # offered in the settings form
MIN_KEEP, MAX_KEEP = 1, 365
DEFAULT_SETTINGS_INTERVAL_HOURS = 24
DEFAULT_SETTINGS_KEEP = 14
SCHEDULER_CHECK_INTERVAL_S = 15 * 60   # how often the background thread checks
_TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"
_NAME_RE = re.compile(r"^(?P<stem>[\w.-]+?)-(?P<ts>\d{8}-\d{6})-(?P<reason>[a-z0-9.-]+)\.db$")
_SETTING_PREFIX = "backup."


class BackupError(Exception):
    """A backup operation failed; the message is safe to show to the user."""


@dataclass(frozen=True)
class BackupSettings:
    """User-configurable backup behaviour.

    Attributes:
        enabled: Whether automatic backups run.
        interval_hours: Minimum time between two automatic backups.
        keep: Number of automatic backups kept; older ones are deleted.
        directory: Target folder; empty means ``backups/`` next to the database.
    """

    enabled: bool = True
    interval_hours: int = DEFAULT_SETTINGS_INTERVAL_HOURS
    keep: int = DEFAULT_SETTINGS_KEEP
    directory: str = ""


@dataclass(frozen=True)
class BackupInfo:
    """One backup file found on disk."""

    name: str
    path: str
    created: datetime
    reason: str
    size_bytes: int

    @property
    def kind(self) -> str:
        """``auto``, ``manual``, ``restore`` or ``migration`` (for labels and styling)."""
        if self.reason.startswith(REASON_AUTO):
            return "auto"
        if self.reason.startswith(REASON_MANUAL):
            return "manual"
        if self.reason.startswith(REASON_RESTORE):
            return "restore"
        return "migration"


# ── Settings ──────────────────────────────────────────────────────────────────

def load_settings() -> BackupSettings:
    """Read the backup settings, falling back to the defaults for missing keys."""
    rows = {
        row.key.removeprefix(_SETTING_PREFIX): row.value
        for row in AppSetting.query.filter(AppSetting.key.startswith(_SETTING_PREFIX)).all()
    }
    defaults = BackupSettings()
    try:
        return BackupSettings(
            enabled=rows.get("enabled", str(defaults.enabled)) == "True",
            interval_hours=int(rows.get("interval_hours", defaults.interval_hours)),
            keep=int(rows.get("keep", defaults.keep)),
            directory=rows.get("directory", defaults.directory),
        )
    except ValueError:  # hand-edited database
        logger.warning("Invalid backup settings in the database; using defaults")
        return defaults


def save_settings(settings: BackupSettings) -> None:
    """Validate and store the backup settings.

    Raises:
        BackupError: If a value is out of range or the folder is not writable.
    """
    if settings.interval_hours not in INTERVAL_CHOICES_HOURS:
        raise BackupError(f"interval must be one of {INTERVAL_CHOICES_HOURS}")
    if not MIN_KEEP <= settings.keep <= MAX_KEEP:
        raise BackupError(f"keep must be between {MIN_KEEP} and {MAX_KEEP}")
    if settings.directory:
        _ensure_writable(_resolve_dir(settings.directory))
    for key, value in (
        ("enabled", str(settings.enabled)),
        ("interval_hours", str(settings.interval_hours)),
        ("keep", str(settings.keep)),
        ("directory", settings.directory),
    ):
        db.session.merge(AppSetting(key=_SETTING_PREFIX + key, value=value))
    db.session.commit()


# ── Locations ─────────────────────────────────────────────────────────────────

def database_path() -> str | None:
    """Path of the SQLite database file, or ``None`` for other databases."""
    url = db.engine.url
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    return os.path.abspath(url.database)


def default_dir() -> str | None:
    """``backups/`` next to the database file (also used before schema upgrades)."""
    path = database_path()
    return os.path.join(os.path.dirname(path), "backups") if path else None


def backup_dir(settings: BackupSettings | None = None) -> str | None:
    """The folder new backups are written to."""
    settings = settings or load_settings()
    return _resolve_dir(settings.directory) if settings.directory else default_dir()


def _resolve_dir(directory: str) -> str:
    """Absolute folder; relative paths are taken relative to the project root (or the exe)."""
    if os.path.isabs(directory):
        return os.path.normpath(directory)
    return os.path.normpath(os.path.join(APP_HOME, directory))


def _ensure_writable(directory: str) -> None:
    """Create the folder if needed and check that files can be written there."""
    try:
        os.makedirs(directory, exist_ok=True)
        with tempfile.TemporaryFile(dir=directory):
            pass
    except OSError as e:
        raise BackupError(f"{directory}: {e.strerror or e}") from e


# ── Creating and listing ──────────────────────────────────────────────────────

def create_backup(reason: str, directory: str | None = None) -> BackupInfo:
    """Copy the database into the backup folder.

    Args:
        reason: Suffix of the file name (``auto``, ``manual``, ``before-...``).
        directory: Target folder; defaults to the configured one. Passed
            explicitly before schema upgrades, when the settings table may not
            exist yet.

    Returns:
        The new backup.

    Raises:
        BackupError: For non-SQLite databases or when the copy fails.
    """
    source = database_path()
    if source is None or not os.path.isfile(source):
        raise BackupError("backups require a SQLite database file")
    target_dir = directory or backup_dir()
    assert target_dir is not None  # a SQLite file always has a default folder
    _ensure_writable(target_dir)

    stem = os.path.splitext(os.path.basename(source))[0]
    stamp = datetime.now().strftime(_TIMESTAMP_FORMAT)
    name = f"{stem}-{stamp}-{reason}.db"
    counter = 2
    while os.path.exists(os.path.join(target_dir, name)):  # two backups in one second
        name = f"{stem}-{stamp}-{reason}.{counter}.db"
        counter += 1
    target = os.path.join(target_dir, name)
    try:
        with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
    except sqlite3.Error as e:
        raise BackupError(str(e)) from e
    logger.info("Database backed up to %s", target)
    info = _info(target)
    assert info is not None
    return info


def _info(path: str) -> BackupInfo | None:
    """Parse a backup file name; ``None`` for files that are not backups."""
    name = os.path.basename(path)
    match = _NAME_RE.match(name)
    if not match:
        return None
    try:
        created = datetime.strptime(match["ts"], _TIMESTAMP_FORMAT)
        size = os.path.getsize(path)
    except (ValueError, OSError):
        return None
    return BackupInfo(name, path, created, match["reason"], size)


def list_backups() -> list[BackupInfo]:
    """All backups in the configured and the default folder, newest first."""
    folders = {d for d in (backup_dir(), default_dir()) if d and os.path.isdir(d)}
    found: dict[str, BackupInfo] = {}
    for folder in folders:
        for entry in os.scandir(folder):
            info = _info(entry.path) if entry.is_file() else None
            if info and info.name not in found:
                found[info.name] = info
    return sorted(found.values(), key=lambda b: (b.created, b.name), reverse=True)


def find_backup(name: str) -> BackupInfo | None:
    """Look a backup up by file name; only names from :func:`list_backups` match."""
    return next((b for b in list_backups() if b.name == name), None)


def delete_backup(name: str) -> bool:
    """Delete one backup. Returns ``False`` if it does not exist."""
    info = find_backup(name)
    if info is None:
        return False
    os.remove(info.path)
    logger.info("Deleted backup %s", info.path)
    return True


def prune(settings: BackupSettings | None = None) -> int:
    """Delete the oldest automatic backups beyond ``settings.keep``.

    Returns:
        The number of files deleted.
    """
    settings = settings or load_settings()
    autos = [b for b in list_backups() if b.kind == "auto"]
    stale = autos[settings.keep:]
    for info in stale:
        os.remove(info.path)
        logger.info("Pruned old automatic backup %s", info.path)
    return len(stale)


# ── Restoring ─────────────────────────────────────────────────────────────────

def restore_backup(name: str) -> BackupInfo:
    """Replace the database contents with a backup.

    The current state is saved first (``before-restore``), so a restore can
    itself be undone. Afterwards the schema is upgraded in case the backup is
    older than the running version.

    Raises:
        BackupError: If the backup does not exist or is not a SpendScope database.
    """
    info = find_backup(name)
    if info is None:
        raise BackupError("backup not found")
    _check_is_spendscope_db(info.path)
    create_backup(REASON_RESTORE)

    db.session.remove()
    db.engine.dispose()  # no pooled connection may hold the old file open
    target = database_path()
    assert target is not None
    try:
        with closing(sqlite3.connect(info.path)) as src, closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
    except sqlite3.Error as e:
        raise BackupError(str(e)) from e
    db.engine.dispose()

    from app import _upgrade_database

    _upgrade_database()
    logger.info("Restored database from %s", info.path)
    return info


def _check_is_spendscope_db(path: str) -> None:
    """Raise BackupError unless the file is a SQLite database with an expenses table."""
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error as e:
        raise BackupError(str(e)) from e
    if "expenses" not in tables:
        raise BackupError("not a SpendScope database")


# ── Scheduling ────────────────────────────────────────────────────────────────

def last_auto_backup() -> BackupInfo | None:
    """The newest automatic backup, if any."""
    return next((b for b in list_backups() if b.kind == "auto"), None)


def next_due(settings: BackupSettings | None = None) -> datetime | None:
    """When the next automatic backup is due (``None`` if disabled)."""
    settings = settings or load_settings()
    if not settings.enabled:
        return None
    last = last_auto_backup()
    return last.created + timedelta(hours=settings.interval_hours) if last else datetime.now()


def run_due_backup(now: datetime | None = None) -> BackupInfo | None:
    """Make an automatic backup if one is due, then prune old ones.

    Args:
        now: Injectable current time for tests.

    Returns:
        The new backup, or ``None`` if none was due or the database is not SQLite.
    """
    if database_path() is None:
        return None
    settings = load_settings()
    due = next_due(settings)
    if due is None or (now or datetime.now()) < due:
        return None
    info = create_backup(REASON_AUTO, backup_dir(settings))
    prune(settings)
    return info


def start_scheduler(app: Flask, interval_s: float = SCHEDULER_CHECK_INTERVAL_S) -> threading.Thread:
    """Run :func:`run_due_backup` now and then every ``interval_s`` seconds.

    The thread is a daemon, so it ends with the app. Failures are logged and
    retried at the next check instead of stopping the scheduler.
    """

    def loop() -> None:
        while True:
            with app.app_context():
                try:
                    run_due_backup()
                except (BackupError, OSError, sqlite3.Error):
                    logger.exception("Automatic backup failed")
                finally:
                    db.session.remove()
            time.sleep(interval_s)

    thread = threading.Thread(target=loop, name="backup-scheduler", daemon=True)
    thread.start()
    return thread
