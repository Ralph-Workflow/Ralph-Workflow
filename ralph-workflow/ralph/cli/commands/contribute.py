""":star: ``ralph contribute`` — open the Codeberg repo to star and fork Ralph Workflow.

This is a lightweight community-support command. It opens the canonical
Codeberg repository in the default browser so you can star the project,
watch for releases, or fork it — all from a single CLI invocation.

Alias: `ralph star` is a shortcut that does the same thing.

No git repository, configuration, or authentication is required.

P2 (wt-028-display S-14): all visible output routes through the shared
``ParallelDisplay`` surface (``emit_status`` / ``emit_info_panel`` /
``emit_renderable`` / ``emit_warning``) so the drift-prevention suite
can verify no command reaches the terminal through its own path.
"""

from __future__ import annotations

import webbrowser
from typing import Annotated

import typer
from rich.panel import Panel
from rich.text import Text

from ralph.display.context import make_display_context
from ralph.display.parallel_display import resolve_active_display

CODEBERG_REPO = "https://codeberg.org/RalphWorkflow/Ralph-Workflow"
GITHUB_REPO = "https://github.com/Ralph-Workflow/Ralph-Workflow"
PROJECT_NAME = "Ralph Workflow"


def _build_banner() -> Text:
    """Build the rich-starred contribute banner."""
    return Text.from_markup(
        "\n"
        "  [bold theme.banner.title]✨ Ralph Workflow[/bold theme.banner.title]\n"
        "  [theme.text.muted]Multi-agent AI orchestration pipeline[/theme.text.muted]\n"
        "\n"
        "  [bold]⭐ Star &amp; fork the project[/bold]\n"
        "  [theme.text.muted]"
        "Every star helps more developers discover the tool."
        "[/theme.text.muted]\n"
    )


def contribute(
    source: Annotated[
        str,
        typer.Option(
            "--source",
            "-s",
            help="Repo source to open: 'codeberg' (default) or 'github'",
        ),
    ] = "codeberg",
) -> None:
    """Open the Ralph Workflow repo in your browser so you can star it.

    Opens the Codeberg project page (default) or GitHub mirror so you can
    star, watch, or fork — then come back and keep working.

    Examples:
        ralph contribute              # Open Codeberg
        ralph contribute --source github  # Open GitHub mirror
    """
    ctx = make_display_context()
    display = resolve_active_display(None, ctx)
    source_lower = source.lower()
    if source_lower == "github":
        url = GITHUB_REPO
        label = "GitHub mirror"
    elif source_lower == "codeberg":
        url = CODEBERG_REPO
        label = "Codeberg"
    else:
        display.emit_warning(
            f"Unknown source '{source}'. Use 'codeberg' (default) or 'github'."
        )
        raise typer.Exit(1)

    banner = _build_banner()
    display.emit_renderable(banner)

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
