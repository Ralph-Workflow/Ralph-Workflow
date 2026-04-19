"""Analysis panel showing the latest analysis decision and reason."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from ralph.display.theme import RALPH_THEME, format_status

if TYPE_CHECKING:
    from rich.theme import Theme

    from ralph.display.snapshot import DashboardSnapshot


_DECISION_TO_SEMANTIC: dict[str, str] = {
    "proceed": "success",
    "approve": "success",
    "approved": "success",
    "complete": "success",
    "success": "success",
    "revise": "warning",
    "loopback": "warning",
    "needs_changes": "warning",
    "needs_work": "warning",
    "partial": "warning",
    "escalate": "info",
    "escalation": "info",
    "failure": "error",
    "failed": "error",
    "fail": "error",
    "error": "error",
}


_MAX_REASON_CHARS = 220


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def _phase_label(phase: str) -> str:
    return phase.replace("_", " ").title()


class AnalysisPanel:
    name = "analysis"

    def render(
        self,
        snapshot: DashboardSnapshot,
        *,
        theme: Theme = RALPH_THEME,
        width: int | None = None,
    ) -> Panel:
        del width
        border = theme.styles.get("theme.panel.border", "")

        if snapshot.analysis_decision is None and snapshot.analysis_phase is None:
            content = Text.from_markup("[dim]awaiting analysis[/dim]")
            return Panel(content, title="Analysis", border_style=border, padding=(0, 1))

        parts: list[str] = []
        if snapshot.analysis_phase:
            parts.append(f"[bold]{escape(_phase_label(snapshot.analysis_phase))}[/]")
        if snapshot.analysis_decision:
            decision_key = snapshot.analysis_decision.lower()
            semantic = _DECISION_TO_SEMANTIC.get(decision_key, "info")
            parts.append(format_status(semantic) + f" [bold]{escape(decision_key)}[/]")
        header = "  ".join(parts) if parts else ""
        lines: list[str] = []
        if header:
            lines.append(header)
        if snapshot.analysis_reason:
            reason = _truncate(snapshot.analysis_reason, _MAX_REASON_CHARS)
            lines.append(f"[dim]{escape(reason)}[/dim]")

        return Panel(
            Text.from_markup("\n".join(lines)),
            title="Analysis",
            border_style=border,
            padding=(0, 1),
        )


analysis_panel = AnalysisPanel()
