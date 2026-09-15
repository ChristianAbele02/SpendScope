"""Entry point: ``python run.py`` starts the server, ``flask --app run import-csv`` imports."""
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


if __name__ == "__main__":
    # Binds 0.0.0.0 so phones on the LAN can reach /scan; debug stays off
    # unless FLASK_DEBUG=1 because the debugger would be exposed too.
    app.run(debug=app.config.get("DEBUG", False), host="0.0.0.0", port=5000)
