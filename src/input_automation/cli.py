"""CLI entry point for input-automation."""


import typer

from input_automation import __version__

app = typer.Typer(help="Automate inputs on Linux (Wayland).")


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"input-automation {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool | None = typer.Option(
        None,
        "--version",
        callback=_print_version,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Automate inputs on Linux (Wayland) with device- and vision-driven macros."""


if __name__ == "__main__":
    app()
