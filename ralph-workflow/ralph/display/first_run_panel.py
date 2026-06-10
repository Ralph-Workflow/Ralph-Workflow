"""First-run welcome panel rendering helper for ralph.config.welcome.

This module consolidates the Panel construction previously inlined in
``ralph.config.welcome`` so that ALL display emission in Ralph Workflow
flows through the ``ralph.display`` surface. The helper takes a
``DisplayContext`` (NOT a separate Console) so the panel is emitted on
the same console as the rest of the DisplayContext-driven output.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.panel import Panel

if TYPE_CHECKING:
    from rich.console import RenderableType

    from ralph.display.context import DisplayContext


def render_first_run_panel(
    content: list[RenderableType],
    display_context: DisplayContext,
) -> None:
    """Print the first-run welcome Panel to ``display_context.console``.

    Args:
        content: The list of renderables that fill the Panel body.
        display_context: Display context for adaptive layout (required).
            The helper uses ``display_context.console`` exclusively;
            no separate ``Console`` is constructed.
    """
    panel = Panel(
        Group(*content),
        title="Ralph Workflow first-run setup",
        border_style="theme.banner.border",
        padding=(1, 2),
    )
    display_context.console.print(panel)
