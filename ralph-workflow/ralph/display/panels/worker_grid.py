"""Worker grid panel for up to 4 workers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.columns import Columns
from rich.panel import Panel
from rich.text import Text

from ralph.display.theme import RALPH_THEME, format_status

if TYPE_CHECKING:
    from rich.theme import Theme

    from ralph.display.snapshot import DashboardSnapshot


MAX_GRID_WORKERS = 4


def _truncate(text: str, max_cells: int) -> str:
    if cell_len(text) <= max_cells:
        return text
    result = []
    used = 0
    for c in text:
        cl = cell_len(c)
        if used + cl > max_cells:
            break
        result.append(c)
        used += cl
    return "".join(result) + "…"


class WorkerGridPanel:
    name = "worker_grid"

    def render(
        self,
        snapshot: DashboardSnapshot,
        *,
        theme: Theme = RALPH_THEME,
        width: int | None = None,
    ) -> Panel | Columns:
        workers = snapshot.workers
        if len(workers) == 0:
            return Panel(
                Text.from_markup("[dim]no workers[/dim]"),
                title="Workers",
                border_style=theme.styles.get("theme.panel.border", ""),
            )
        if len(workers) > MAX_GRID_WORKERS:
            return Panel(
                Text.from_markup("[dim]use list view (>4 workers)[/dim]"),
                title="Workers",
                border_style=theme.styles.get("theme.panel.border", ""),
            )

        panels = []
        for w in workers:
            title = f"{format_status(w.status_semantic)} {_truncate(w.unit_id, 16)}"
            lines = [f"[theme.text.muted]{_truncate(w.description, 40)}[/] "]
            lines.append(f"[theme.text.muted]elapsed: {w.elapsed_s:.1f}s[/]")
            if w.commit_sha:
                lines.append(f"[theme.text.muted]commit: {w.commit_sha[:7]}[/]")
            if w.error_message:
                lines.append(f"[bold red]{_truncate(w.error_message, 80)}[/]")
            content = Text.from_markup("\n".join(lines))
            border = theme.styles.get(f"theme.status.{w.status_semantic}", "")
            panels.append(Panel(content, title=title, border_style=border, padding=(0, 1)))

        return Columns(panels, equal=True, expand=True)


worker_grid_panel = WorkerGridPanel()
