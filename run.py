import click
from app import create_app, db

app = create_app()


@app.cli.command("import-csv")
@click.argument("path", default=None, required=False)
def import_csv_command(path):
    """Import expenses from CSV file into the database."""
    from app.parser import import_csv
    from config import Config

    csv_path = path or Config.CSV_PATH
    click.echo(f"Importing from {csv_path} ...")
    result = import_csv(csv_path)
    if result["success"]:
        click.echo(f"Done. Imported: {result['imported']}, Skipped: {result['skipped']}")
    else:
        click.echo(f"Error: {result['error']}", err=True)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
