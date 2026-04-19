"""Plan panel showing PROMPT.md preview."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from ralph.display.theme import RALPH_THEME

if TYPE_CHECKING:
    from rich.theme import Theme

    from ralph.display.snapshot import DashboardSnapshot


class PlanPanel:
    name = "plan"

    def render(
        self,
        snapshot: DashboardSnapshot,
        *,
        theme: Theme = RALPH_THEME,
        width: int | None = None,
    ) -> Panel:
        title = f"Plan: {escape(snapshot.prompt_path)}" if snapshot.prompt_path else "Plan"

        if not snapshot.prompt_preview:
            content = Text("[dim]no preview available[/dim]")
        else:
            joined = "\n".join(snapshot.prompt_preview)
            content = Text(joined)

        return Panel(
            content,
            title=title,
            border_style=theme.styles.get("theme.panel.border", ""),
            padding=(0, 1),
        )


plan_panel = PlanPanel()
