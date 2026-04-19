"""Worker list panel for more than 4 workers."""

from __future__ import annotations

from functools import cmp_to_key
from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ralph.display.theme import RALPH_THEME, format_status

if TYPE_CHECKING:
    from rich.theme import Theme

    from ralph.display.snapshot import DashboardSnapshot, WorkerSnapshot


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


def _worker_cmp(a: WorkerSnapshot, b: WorkerSnapshot) -> int:
    status_order = {"error": 0, "running": 1, "pending": 2, "success": 3}
    a_order = status_order.get(a.status_semantic, 99)
    b_order = status_order.get(b.status_semantic, 99)
    if a_order != b_order:
        return a_order - b_order
    if a.status_semantic == "running":
        return int((b.elapsed_s - a.elapsed_s) * 1000)
    if a.status_semantic == "success":
        # Most recent first: None finished_at sorts last
        if a.finished_at is None or b.finished_at is None:
            return 0 if a.finished_at == b.finished_at else (1 if a.finished_at is None else -1)
        return -1 if a.finished_at > b.finished_at else (1 if a.finished_at < b.finished_at else 0)
    return 0


class WorkerListPanel:
    name = "worker_list"

    MAX_LIST_WORKERS = 4
    COMPACT_WIDTH_THRESHOLD = 80

    def render(
        self,
        snapshot: DashboardSnapshot,
        *,
        theme: Theme = RALPH_THEME,
        width: int | None = None,
    ) -> Panel | Text:
        workers = snapshot.workers
        if len(workers) == 0:
            return Panel(
                Text("[dim]no workers[/dim]"),
                title="Workers",
                border_style=theme.styles.get("theme.panel.border", ""),
            )
        if len(workers) <= self.MAX_LIST_WORKERS:
            return Panel(
                Text("[dim]use grid view (≤4 workers)[/dim]"),
                title="Workers",
                border_style=theme.styles.get("theme.panel.border", ""),
            )

        # Sort: failed → running (longest elapsed first) → pending → succeeded
        sorted_workers = sorted(workers, key=cmp_to_key(_worker_cmp))

        table = Table(show_header=True, border_style=theme.styles["theme.panel.border"])
        table.add_column("Status", style="bold")
        table.add_column("Unit ID", max_width=24)
        table.add_column("Phase/Status")
        table.add_column("Elapsed", justify="right")

        # Hide Description in compact mode (width < 80)
        is_compact = width is not None and width < self.COMPACT_WIDTH_THRESHOLD
        if not is_compact:
            table.add_column("Description", max_width=40)
        table.add_column("Last")

        for w in sorted_workers:
            status_badge = format_status(w.status_semantic)
            unit_id_cell = _truncate(w.unit_id, 24)
            phase = snapshot.phase
            elapsed_s = f"{w.elapsed_s:.1f}s"
            description_cell = _truncate(w.description, 40)

            # Last column: truncated commit_sha or error_message
            if w.error_message:
                last_cell = _truncate(w.error_message, 20)
            elif w.commit_sha:
                last_cell = _truncate(w.commit_sha[:7], 20)
            else:
                last_cell = ""

            row_style = theme.styles.get(f"theme.status.{w.status_semantic}", "")

            if is_compact:
                table.add_row(
                    status_badge,
                    unit_id_cell,
                    phase,
                    elapsed_s,
                    last_cell,
                    style=row_style,
                )
            else:
                table.add_row(
                    status_badge,
                    unit_id_cell,
                    phase,
                    elapsed_s,
                    description_cell,
                    last_cell,
                    style=row_style,
                )

        return Panel(table, title="Workers", border_style=theme.styles["theme.panel.border"])


worker_list_panel = WorkerListPanel()
