""":star: ``ralph contribute`` — open the GitHub repo to star and fork Ralph Workflow.

This is a lightweight community-support command. It opens the canonical
GitHub repository by default; the Codeberg mirror remains available.
"""

from __future__ import annotations

import webbrowser
from typing import Annotated

import typer
from rich.panel import Panel
from rich.text import Text

from ralph.display.context import make_display_context
from ralph.display.parallel_display import resolve_active_display
from ralph.project_urls import CODEBERG_MIRROR_URL, GITHUB_REPOSITORY_URL

PROJECT_NAME = "Ralph Workflow"


def _build_banner() -> Text:
    """Build the rich-starred contribute banner."""
    return Text.from_markup(
        "\n"
        "  [bold theme.banner.title]✨ Ralph Workflow[/bold theme.banner.title]\n"
        "  [theme.text.muted]Multi-agent AI orchestration pipeline[/theme.text.muted]\n"
        "\n"
        "  [bold]⭐ Star &amp; fork the project[/bold]\n"
        "  [theme.text.muted]Every star helps more developers discover the tool.[/theme.text.muted]\n"
    )


def contribute(
    source: Annotated[
        str,
        typer.Option(
            "--source",
            "-s",
            help="Repo source to open: 'github' (default) or 'codeberg' mirror",
        ),
    ] = "github",
) -> None:
    """Open the Ralph Workflow repo in your browser so you can star it.

    Opens the GitHub project page (default) or Codeberg mirror so you can
    star, watch, or fork — then come back and keep working.

    Examples:
        ralph contribute                    # Open GitHub
        ralph contribute --source codeberg  # Open Codeberg mirror
    """
    ctx = make_display_context()
    display = resolve_active_display(None, ctx)
    source_lower = source.lower()
    if source_lower == "github":
        url = GITHUB_REPOSITORY_URL
        label = "GitHub"
    elif source_lower == "codeberg":
        url = CODEBERG_MIRROR_URL
        label = "Codeberg mirror"
    else:
        display.emit_warning(f"Unknown source '{source}'. Use 'github' (default) or 'codeberg'.")
        raise typer.Exit(1)

    display.emit_renderable(_build_banner())
    panel = Panel(
        Text.from_markup(
            f"  Opening [bold link={url}]{label} repo[/bold link] in your browser...\n"
            f"  [link={url}]{url}[/link]"
        ),
        title="📂 Contribute",
        border_style="green",
    )
    display.emit_renderable(panel)

    try:
        opened = webbrowser.open(url)
    except Exception as exc:
        display.emit_warning(f"Could not open browser: {exc}")
        display.emit_warning(f"Visit: {url}")
        raise typer.Exit(1) from exc

    if not opened:
        display.emit_warning(f"Could not open browser automatically. Visit: {url}")

    display.emit_renderable(
        Text.from_markup("\n[green]Thank you for supporting Ralph Workflow![/green] ⭐\n")
    )
