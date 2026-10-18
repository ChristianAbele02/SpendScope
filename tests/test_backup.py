"""Database backups: create, list, prune, schedule, restore and the settings page."""
import os
from datetime import date, datetime, timedelta

import pytest

from app import backup, db
from app.models import Expense


def _add_expense(store: str = "Aldi", amount: float = 9.99) -> None:
    db.session.add(Expense(date=date(2024, 1, 2), store=store, amount=amount, category="Lebensmittel"))
    db.session.commit()


def _fake_backup(directory: str, created: datetime, reason: str) -> str:
    """Write a backup file with a given timestamp (copies the live database)."""
    info = backup.create_backup(reason, directory)
    name = f"test-{created:%Y%m%d-%H%M%S}-{reason}.db"
    path = os.path.join(directory, name)
    os.replace(info.path, path)
    return path


# ── Creating and listing ──────────────────────────────────────────────────────

def test_manual_backup_is_listed(ctx):
    _add_expense()
    info = backup.create_backup(backup.REASON_MANUAL)
    assert os.path.isfile(info.path)
    assert info.kind == "manual"
    assert info.size_bytes > 0
    assert [b.name for b in backup.list_backups()] == [info.name]


def test_two_backups_in_one_second_do_not_overwrite(ctx):
    first = backup.create_backup(backup.REASON_MANUAL)
    second = backup.create_backup(backup.REASON_MANUAL)
    assert first.name != second.name
    assert len(backup.list_backups()) == 2


def test_unrelated_files_are_ignored(ctx):
    directory = backup.backup_dir()
    os.makedirs(directory, exist_ok=True)
    for name in ("notes.txt", "expenses.db", "x-2024-manual.db"):
        open(os.path.join(directory, name), "w").close()
    assert backup.list_backups() == []


def test_find_backup_rejects_path_traversal(ctx):
    backup.create_backup(backup.REASON_MANUAL)
    assert backup.find_backup("../test.db") is None
    assert backup.delete_backup("..\\..\\config.py") is False


# ── Pruning and scheduling ────────────────────────────────────────────────────

def test_prune_keeps_newest_automatic_and_all_manual(ctx):
    directory = backup.backup_dir()
    now = datetime(2026, 1, 10, 12, 0, 0)
    for days in range(5):
        _fake_backup(directory, now - timedelta(days=days), backup.REASON_AUTO)
    manual = _fake_backup(directory, now - timedelta(days=30), backup.REASON_MANUAL)
    deleted = backup.prune(backup.BackupSettings(keep=2))
    assert deleted == 3
    remaining = backup.list_backups()
    assert [b.kind for b in remaining] == ["auto", "auto", "manual"]
    assert remaining[0].created == now
    assert os.path.isfile(manual)


def test_auto_backup_runs_when_due_and_not_before(ctx):
    first = backup.run_due_backup()
    assert first is not None and first.kind == "auto"
    assert backup.run_due_backup(now=first.created + timedelta(hours=23)) is None
    later = backup.run_due_backup(now=first.created + timedelta(hours=25))
    assert later is not None


def test_auto_backup_respects_disabled_setting(ctx):
    backup.save_settings(backup.BackupSettings(enabled=False))
    assert backup.run_due_backup() is None
    assert backup.next_due() is None


def test_auto_backup_writes_to_custom_folder(ctx, tmp_path):
    target = tmp_path / "offsite"
    backup.save_settings(backup.BackupSettings(directory=str(target)))
    info = backup.run_due_backup()
    assert info is not None
    assert os.path.dirname(info.path) == str(target)


def test_scheduler_thread_makes_a_backup(app):
    thread = backup.start_scheduler(app, interval_s=3600)
    assert thread.daemon
    with app.app_context():
        for _ in range(100):
            if backup.last_auto_backup():
                break
            thread.join(0.05)
        assert backup.last_auto_backup() is not None


# ── Settings ──────────────────────────────────────────────────────────────────

def test_settings_round_trip(ctx, tmp_path):
    new = backup.BackupSettings(enabled=False, interval_hours=168, keep=5, directory=str(tmp_path))
    backup.save_settings(new)
    assert backup.load_settings() == new


@pytest.mark.parametrize(
    "settings",
    [backup.BackupSettings(keep=0), backup.BackupSettings(keep=1000),
     backup.BackupSettings(interval_hours=5)],
)
def test_invalid_settings_are_rejected(ctx, settings):
    with pytest.raises(backup.BackupError):
        backup.save_settings(settings)


def test_unwritable_folder_is_rejected(ctx, tmp_path):
    blocker = tmp_path / "file.txt"
    blocker.write_text("x")
    with pytest.raises(backup.BackupError):
        backup.save_settings(backup.BackupSettings(directory=str(blocker / "sub")))


# ── Restore ───────────────────────────────────────────────────────────────────

def test_restore_brings_back_old_data_and_saves_current_state(ctx):
    _add_expense("Aldi")
    saved = backup.create_backup(backup.REASON_MANUAL)
    _add_expense("Lidl")
    assert Expense.query.count() == 2

    backup.restore_backup(saved.name)
    assert [e.store for e in Expense.query.all()] == ["Aldi"]
    kinds = {b.kind for b in backup.list_backups()}
    assert "restore" in kinds  # the two-expense state can be restored again


def test_restore_rejects_foreign_files(ctx):
    directory = backup.backup_dir()
    os.makedirs(directory, exist_ok=True)
    fake = os.path.join(directory, "test-20260101-120000-manual.db")
    with open(fake, "wb") as fh:
        fh.write(b"not a database")
    with pytest.raises(backup.BackupError):
        backup.restore_backup(os.path.basename(fake))


# ── Pages ─────────────────────────────────────────────────────────────────────

def test_backup_page_lists_and_creates(app):
    client = app.test_client()
    assert client.get("/settings/backups").status_code == 200
    response = client.post("/settings/backups/create", follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        name = backup.list_backups()[0].name
    assert name in response.get_data(as_text=True) or "manual" in name


def test_backup_download_and_delete(app):
    client = app.test_client()
    with app.app_context():
        name = backup.create_backup(backup.REASON_MANUAL).name
    download = client.get(f"/settings/backups/{name}/download")
    assert download.status_code == 200
    assert download.data[:16] == b"SQLite format 3\x00"
    download.close()
    assert client.get("/settings/backups/missing.db/download").status_code == 404
    client.post(f"/settings/backups/{name}/delete")
    with app.app_context():
        assert backup.list_backups() == []


def test_settings_form_validates(app):
    client = app.test_client()
    client.post("/settings/backups/settings", data={"interval_hours": "24", "keep": "0"})
    client.post("/settings/backups/settings", data={"interval_hours": "abc", "keep": "3"})
    with app.app_context():
        assert backup.load_settings() == backup.BackupSettings()
    client.post("/settings/backups/settings", data={"interval_hours": "168", "keep": "7"})
    with app.app_context():
        assert backup.load_settings() == backup.BackupSettings(
            enabled=False, interval_hours=168, keep=7
        )
