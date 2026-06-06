"""Star command — open Codeberg and print star CTA for Ralph Workflow."""

from __future__ import annotations

import webbrowser
from typing import Annotated

import typer

CODEBERG_REPO = "https://codeberg.org/RalphWorkflow/Ralph-Workflow"
STAR_MESSAGE = (
    "⭐ Star Ralph Workflow on Codeberg so we know you're using it — "
    "stars drive development priority."
)


def star(
    no_browser: Annotated[
        bool,
        typer.Option("--no-browser", help="Print the link without opening a browser"),
    ] = False,
) -> None:
    """Open the Codeberg repo in your browser — star it if Ralph Workflow helps you.

    Stars are the most visible signal that Ralph Workflow is useful.
    If you use it, let us know with a quick star.
    """
    typer.echo("")
    typer.echo(typer.style("⭐  Ralph Workflow on Codeberg", fg=typer.colors.YELLOW, bold=True))
    typer.echo(f"   {CODEBERG_REPO}")
    typer.echo("")
    typer.echo(f"   {STAR_MESSAGE}")
    typer.echo("")

    if not no_browser:
        try:
            webbrowser.open(CODEBERG_REPO)
            typer.echo(
                typer.style("   → Opened in browser. Click ⭐ to star!", fg=typer.colors.GREEN)
            )
        except Exception:
            typer.echo(
                typer.style(
                    "   (browser could not be opened — copy the link above)",
                    fg=typer.colors.YELLOW,
                )
            )
    else:
        typer.echo("   (use without --no-browser to open in your browser)")
    typer.echo("")
