"""Header panel showing Ralph Workflow title, run-id, and plan path."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from ralph.display.theme import RALPH_THEME

if TYPE_CHECKING:
    from rich.theme import Theme

    from ralph.display.snapshot import DashboardSnapshot

_MAX_RUN_ID_PREVIEW = 8


class HeaderPanel:
    name = "header"

    def render(
        self,
        snapshot: DashboardSnapshot,
        *,
        theme: Theme = RALPH_THEME,
        width: int | None = None,
    ) -> Panel:
        title = Text("Ralph Workflow")
        subtitle_parts = []
        if snapshot.run_id:
            if len(snapshot.run_id) > _MAX_RUN_ID_PREVIEW:
                run_id = snapshot.run_id[:_MAX_RUN_ID_PREVIEW] + "…"
            else:
                run_id = snapshot.run_id
            subtitle_parts.append(f"run: {run_id}")
        if snapshot.prompt_path:
            subtitle_parts.append(escape(snapshot.prompt_path))
        subtitle = (
            " · ".join(subtitle_parts) if subtitle_parts else "[dim]no plan attached[/dim]"
        )

        content = Text(subtitle)
        return Panel(
            content,
            title=title,
            border_style=theme.styles.get("theme.panel.border", ""),
            padding=(0, 1),
        )


header_panel = HeaderPanel()
