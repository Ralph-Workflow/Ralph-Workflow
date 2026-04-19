"""Progress panel with iteration and reviewer pass bars."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.panel import Panel
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn
from rich.text import Text

from ralph.display.theme import RALPH_THEME

if TYPE_CHECKING:
    from rich.theme import Theme

    from ralph.display.snapshot import DashboardSnapshot


class ProgressPanel:
    name = "progress"

    def render(
        self,
        snapshot: DashboardSnapshot,
        *,
        theme: Theme = RALPH_THEME,
        width: int | None = None,
    ) -> Panel:
        if snapshot.total_iterations == 0:
            return Panel(
                Text("[dim]no iteration budget[/dim]"),
                border_style=theme.styles["theme.panel.border"],
            )

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=20),
            TaskProgressColumn(),
        ) as progress:
            progress.add_task(
                "Development",
                total=snapshot.total_iterations,
                completed=snapshot.iteration,
            )
            progress.add_task(
                "Review",
                total=snapshot.total_reviewer_passes,
                completed=snapshot.reviewer_pass,
            )
        return Panel(
            progress.get_renderable(),
            title="Progress",
            border_style=theme.styles["theme.panel.border"],
            padding=(0, 1),
        )


progress_panel = ProgressPanel()
