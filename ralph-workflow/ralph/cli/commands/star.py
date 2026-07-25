"""Star command — open Codeberg and print star CTA for Ralph Workflow.

P2 (wt-028-display S-14): all visible output routes through the shared
``ParallelDisplay`` surface (``emit_status`` / ``emit_info_panel`` /
``emit_renderable`` / ``emit_warning`` / ``emit_blank_line``). The
``typer.style`` decoration on the original output is replaced by the
shared theme styling carried by the display surface.
"""

from __future__ import annotations

import webbrowser
from typing import Annotated

import typer

from ralph.display.context import make_display_context
from ralph.display.parallel_display import resolve_active_display

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
    ctx = make_display_context()
    display = resolve_active_display(None, ctx)
    display.emit_blank_line()
    display.emit_status("⭐  Ralph Workflow on Codeberg")
    display.emit_status(f"   {CODEBERG_REPO}")
    display.emit_blank_line()
    display.emit_status(f"   {STAR_MESSAGE}")
    display.emit_blank_line()

    if not no_browser:
        try:
            webbrowser.open(CODEBERG_REPO)
            display.emit_status("   → Opened in browser. Click ⭐ to star!")
        except Exception:
            display.emit_status(
                "   (browser could not be opened — copy the link above)"
            )
    else:
        display.emit_status("   (use without --no-browser to open in your browser)")
    display.emit_blank_line()
