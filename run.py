"""Entry point.

``python run.py``            opens SpendScope in a native desktop window (pywebview),
                             falling back to server-only mode if pywebview is missing.
``python run.py --server``   runs only the web server (open it in a browser).
``--local``                  in either mode: listen on this PC only (no phone scanning).
``--port N``                 use another port than 5000.
``flask --app run import-csv`` imports historical data.

SpendScope.exe (scripts/build_exe.py) runs this same file with the same options.
Log messages go to data/logs/spendscope.log (and the console, if there is one).
"""
import argparse
import logging
import os
import re
import socket
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from types import TracebackType

import click

from config import FROZEN, LOG_DIR

logger = logging.getLogger(__name__)

LOG_FILE_NAME = "spendscope.log"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 3
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
# Chatty third-party loggers that would flood the log file at INFO level.
_QUIET_LOGGERS = ("alembic.runtime.plugins",)


class _PlainFormatter(logging.Formatter):
    """Formatter that strips the terminal colour codes Werkzeug adds to its messages."""

    def format(self, record: logging.LogRecord) -> str:
        return _ANSI_ESCAPE.sub("", super().format(record))


def _configure_logging() -> None:
    """Log to a rotating file in data/logs/ and, when there is one, the console."""
    # A windowed exe has no console: sys.stdout/stderr are None, and Flask's
    # start-up banner would fail writing to them.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    os.makedirs(LOG_DIR, exist_ok=True)
    file_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, LOG_FILE_NAME),
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(_PlainFormatter(LOG_FORMAT))
    handlers: list[logging.Handler] = [file_handler]
    if not FROZEN:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(LOG_FORMAT))
        handlers.append(console)
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    def log_uncaught(
        exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None
    ) -> None:
        logger.critical("Unhandled exception", exc_info=(exc_type, exc, tb))

    def log_uncaught_in_thread(hook: threading.ExceptHookArgs) -> None:
        logger.critical(
            "Unhandled exception in thread %s",
            hook.thread.name if hook.thread else "?",
            exc_info=hook.exc_value or True,
        )

    sys.excepthook = log_uncaught
    threading.excepthook = log_uncaught_in_thread


if __name__ == "__main__":
    _configure_logging()  # before create_app(), so migrations and backups are logged

from app import create_app  # noqa: E402

LAN_HOST = "0.0.0.0"     # reachable from the network so a paired phone can open /scan
LOCAL_HOST = "127.0.0.1"  # --local: this PC only
DEFAULT_PORT = int(os.environ.get("SPENDSCOPE_PORT", 5000))
WINDOW_TITLE = "SpendScope"
WINDOW_SIZE = (1280, 860)
WINDOW_MIN_SIZE = (900, 600)
SERVER_START_TIMEOUT_S = 10.0
SERVER_POLL_INTERVAL_S = 0.1

app = create_app()
PORT = DEFAULT_PORT  # replaced by --port before the server starts


@app.cli.command("import-csv")
@click.argument("path", default=None, required=False)
@click.option(
    "--replace",
    is_flag=True,
    default=False,
    help="Delete all existing expenses before importing (destructive).",
)
def import_csv_command(path: str | None, replace: bool) -> None:
    """Import expenses from a CSV file (default: CSV_PATH from config).

    New rows are appended and duplicates skipped unless --replace is given.
    The CSV file itself is only read.
    """
    from app.parser import import_csv

    csv_path = path or app.config["CSV_PATH"]
    mode = "replace" if replace else "append (de-duplicating)"
    click.echo(f"Importing from {csv_path} [{mode}] ...")
    result = import_csv(csv_path, clear_existing=replace)
    if result["success"]:
        click.echo(
            f"Done. Imported: {result['imported']}, "
            f"Skipped: {result['skipped']}, "
            f"Duplicates: {result.get('duplicates', 0)}"
        )
    else:
        click.echo(f"Error: {result['error']}", err=True)


def _host() -> str:
    """Address to bind: the network unless LAN access was switched off (--local)."""
    return LAN_HOST if app.config["LAN_ACCESS"] else LOCAL_HOST


@app.cli.command("backup")
def backup_command() -> None:
    """Make a manual database backup (same as Settings > Backups > Back up now)."""
    from app.backup import REASON_MANUAL, BackupError, create_backup

    try:
        info = create_backup(REASON_MANUAL)
    except BackupError as e:
        click.echo(f"Backup failed: {e}", err=True)
        raise SystemExit(1) from e
    click.echo(f"Backup written to {info.path}")


def _start_background_jobs() -> None:
    """Start the automatic backup scheduler (first check runs immediately)."""
    from app.backup import start_scheduler

    # With FLASK_DEBUG=1 the reloader runs the app twice; only the child serves.
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    start_scheduler(app)


def run_server() -> None:
    """Run the Flask server in the foreground (browser mode)."""
    _start_background_jobs()
    # Debug stays off unless FLASK_DEBUG=1 because the debugger would be
    # exposed to the whole LAN.
    app.run(debug=app.config.get("DEBUG", False), host=_host(), port=PORT)


def _wait_for_server(port: int, timeout: float) -> bool:
    """Poll until something accepts connections on localhost:port.

    Args:
        port: TCP port to probe.
        timeout: Maximum seconds to wait.

    Returns:
        True if the port became reachable within the timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=SERVER_POLL_INTERVAL_S):
                return True
        except OSError:
            time.sleep(SERVER_POLL_INTERVAL_S)
    return False


def _port_in_use(port: int) -> bool:
    """Whether something already accepts connections on localhost:port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=SERVER_POLL_INTERVAL_S):
            return True
    except OSError:
        return False


def _show_error(message: str) -> None:
    """Report a start-up problem: a message box on Windows, else the log only."""
    logger.error(message)
    if sys.platform == "win32":
        import ctypes

        mb_iconerror = 0x10
        ctypes.windll.user32.MessageBoxW(None, message, WINDOW_TITLE, mb_iconerror)


def run_desktop() -> None:
    """Open SpendScope in a native window; the server runs in a background thread.

    Unless started with --local, the server also listens on the network, so a
    paired phone can scan receipts while the window is open. Closing the window
    ends the process (the thread is a daemon). Falls back to :func:`run_server`
    if pywebview is not installed.
    """
    try:
        import webview
    except ImportError:
        logger.warning(
            "pywebview is not installed (pip install -e \".[desktop]\"); "
            "starting server only, open http://localhost:%d in a browser.",
            PORT,
        )
        run_server()
        return

    _start_background_jobs()
    # The reloader only works in the main thread, so it is always off here.
    server = threading.Thread(
        target=app.run,
        kwargs={"host": _host(), "port": PORT, "use_reloader": False},
        daemon=True,
    )
    server.start()
    if not _wait_for_server(PORT, SERVER_START_TIMEOUT_S):
        _show_error(f"The SpendScope server did not start on port {PORT}. See {LOG_DIR}.")
        sys.exit(1)

    webview.settings["ALLOW_DOWNLOADS"] = True  # CSV/JSON export
    webview.create_window(
        WINDOW_TITLE,
        f"http://127.0.0.1:{PORT}",
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=WINDOW_MIN_SIZE,
    )
    webview.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Start SpendScope.")
    parser.add_argument(
        "--server",
        action="store_true",
        help="Run only the web server instead of opening a desktop window.",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Listen on this PC only; phones cannot connect (disables receipt scanning).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to listen on (default {DEFAULT_PORT}, or SPENDSCOPE_PORT).",
    )
    args = parser.parse_args()
    PORT = args.port
    app.config["LAN_ACCESS"] = not args.local
    if _port_in_use(PORT):
        _show_error(
            f"SpendScope is already running, or another program uses port {PORT}.\n"
            f"Close it first, or start SpendScope with --port <number>."
        )
        sys.exit(1)
    if args.server:
        run_server()
    else:
        run_desktop()
