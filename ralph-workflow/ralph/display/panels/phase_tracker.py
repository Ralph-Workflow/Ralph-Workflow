"""Phase tracker panel showing pipeline phase progress."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from ralph.display.theme import RALPH_THEME

if TYPE_CHECKING:
    from rich.theme import Theme

    from ralph.display.snapshot import DashboardSnapshot

KNOWN_PHASES = (
    "planning",
    "development",
    "development_analysis",
    "development_commit",
    "review",
    "review_analysis",
    "fix",
    "review_commit",
    "merge_integration",
    "complete",
    "failed",
)

_MAX_ERROR_LENGTH = 200


class PhaseTrackerPanel:
    """Panel displaying current pipeline phase with progress indicators."""

    name = "phase_tracker"

    def render(
        self,
        snapshot: DashboardSnapshot,
        *,
        theme: Theme = RALPH_THEME,
        width: int | None = None,
    ) -> Panel:
        phase = snapshot.phase
        phase_display = phase.replace("_", " ").title()

        lines = []
        if snapshot.interrupted_by_user:
            lines.append("[bold orange]⚠ INTERRUPTED[/] ")

        lines.append(f"[bold]{phase_display}[/] ")
        lines.append(f"Iteration {snapshot.iteration}/{snapshot.total_iterations}")
        if snapshot.reviewer_pass > 0:
            lines.append(f" · Review {snapshot.reviewer_pass}/{snapshot.total_reviewer_passes}")

        if snapshot.last_error:
            error = snapshot.last_error[:_MAX_ERROR_LENGTH]
            lines.append(f"\n[bold red]✗ {escape(error)}[/]")

        activity_lines: list[str] = []
        if snapshot.active_agent:
            activity_lines.append(f"Agent: {escape(snapshot.active_agent)}")
        if snapshot.active_tool:
            activity_lines.append(f"Tool: {escape(snapshot.active_tool)}")
        if snapshot.active_path:
            activity_lines.append(f"Path: {escape(snapshot.active_path)}")
        if snapshot.active_workdir:
            activity_lines.append(f"Workdir: {escape(snapshot.active_workdir)}")
        if snapshot.active_command:
            activity_lines.append(f"Command: {escape(snapshot.active_command)}")
        if snapshot.last_activity_line:
            activity_lines.append(escape(snapshot.last_activity_line))
        if activity_lines:
            lines.append("\n" + "\n".join(f"[dim]{entry}[/]" for entry in activity_lines))

        content = Text.from_markup("".join(lines))
        return Panel(
            content,
            title="Phase",
            border_style=theme.styles.get("theme.panel.border", ""),
            padding=(0, 1),
        )


phase_tracker_panel = PhaseTrackerPanel()
