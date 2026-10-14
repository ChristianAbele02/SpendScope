"""Entry point.

``python run.py``            opens SpendScope in a native desktop window (pywebview),
                             falling back to server-only mode if pywebview is missing.
``python run.py --server``   runs only the web server (open it in a browser).
``flask --app run import-csv`` imports historical data.
"""
import argparse
import logging
import socket
import threading
import time

import click

from app import create_app

logger = logging.getLogger(__name__)

HOST = "0.0.0.0"  # LAN-reachable so phones can open /scan
PORT = 5000
WINDOW_TITLE = "SpendScope"
WINDOW_SIZE = (1280, 860)
WINDOW_MIN_SIZE = (900, 600)
SERVER_START_TIMEOUT_S = 10.0
SERVER_POLL_INTERVAL_S = 0.1

app = create_app()


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


def run_server() -> None:
    """Run the Flask server in the foreground (browser mode)."""
    # Debug stays off unless FLASK_DEBUG=1 because the debugger would be
    # exposed to the whole LAN.
    app.run(debug=app.config.get("DEBUG", False), host=HOST, port=PORT)


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


def run_desktop() -> None:
    """Open SpendScope in a native window; the server runs in a background thread.

    The server still binds to the LAN, so phone scanning keeps working while the
    window is open. Closing the window ends the process (the thread is a daemon).
    Falls back to :func:`run_server` if pywebview is not installed.
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

    # The reloader only works in the main thread, so it is always off here.
    server = threading.Thread(
        target=app.run,
        kwargs={"host": HOST, "port": PORT, "use_reloader": False},
        daemon=True,
    )
    server.start()
    if not _wait_for_server(PORT, SERVER_START_TIMEOUT_S):
        raise RuntimeError(f"SpendScope server did not start on port {PORT}")

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
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Start SpendScope.")
    parser.add_argument(
        "--server",
        action="store_true",
        help="Run only the web server instead of opening a desktop window.",
    )
    args = parser.parse_args()
    if args.server:
        run_server()
    else:
        run_desktop()
