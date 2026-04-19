"""Results panel showing final metrics, commit, PR, and verdict."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ralph.display.theme import RALPH_THEME, format_status

if TYPE_CHECKING:
    from rich.theme import Theme

    from ralph.display.snapshot import DashboardSnapshot


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


class ResultsPanel:
    name = "results"

    def render(
        self,
        snapshot: DashboardSnapshot,
        *,
        theme: Theme = RALPH_THEME,
        width: int | None = None,
    ) -> Panel:
        phase = snapshot.phase

        if phase not in ("complete", "failed"):
            return Panel(
                Text("[dim]results pending — pipeline still running[/dim]"),
                title="Results",
                border_style=theme.styles.get("theme.panel.border", ""),
            )

        table = Table(show_header=True, header_style="bold", box=None)
        table.add_column("Metric", style=theme.styles.get("theme.text.emphasis", ""))
        table.add_column("Value", justify="right")
        table.add_row("agent_calls", str(snapshot.total_agent_calls))
        table.add_row("continuations", str(snapshot.total_continuations))
        table.add_row("fallbacks", str(snapshot.total_fallbacks))
        table.add_row("retries", str(snapshot.total_retries))
        table.add_row("push_count", str(snapshot.push_count))

        if phase == "complete":
            border = theme.styles.get("theme.status.success", "")
            completed_text = Text.from_markup(f"{format_status('success')} Pipeline completed")
            content: list[Text | Table] = [completed_text, table]

            if snapshot.pr_url is not None:
                pr_url = snapshot.pr_url
                truncated_url = _truncate(pr_url, 60)
                content.append(Text.from_markup(f"[link={pr_url}]{truncated_url}[/link]"))

            return Panel(Group(*content), title="Results", border_style=border)

        elif phase == "failed":
            if snapshot.last_error is not None:
                truncated_error = _truncate(snapshot.last_error, 200)
                error_text = Text(f"[bold red]Error:[/] {truncated_error}")
            else:
                error_text = Text("[bold red]Error:[/] [dim]unknown error[/dim]")

            border = theme.styles.get("theme.status.error", "")
            return Panel(error_text, title="Results", border_style=border)

        return Panel(
            Text("[dim]results pending[/dim]"),
            title="Results",
            border_style=theme.styles.get("theme.panel.border", ""),
        )


results_panel = ResultsPanel()
