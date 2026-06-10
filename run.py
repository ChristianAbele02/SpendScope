import click

from app import create_app

app = create_app()


@app.cli.command("import-csv")
@click.argument("path", default=None, required=False)
@click.option(
    "--replace",
    is_flag=True,
    default=False,
    help="Delete all existing expenses before importing (destructive).",
)
def import_csv_command(path, replace):
    """Import expenses from a CSV file.

    By default new rows are appended and duplicates are skipped. Pass --replace
    to wipe the table first.
    """
    from app.parser import import_csv
    from config import Config

    csv_path = path or Config.CSV_PATH
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


if __name__ == "__main__":
    # Debug/reloader is off unless FLASK_DEBUG=1; the app binds 0.0.0.0 for
    # phone scanning, so the debugger must not be exposed on a LAN by default.
    app.run(debug=app.config.get("DEBUG", False), host="0.0.0.0", port=5000)
