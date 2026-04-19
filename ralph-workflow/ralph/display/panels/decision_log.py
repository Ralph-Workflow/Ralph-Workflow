"""Decision log panel showing recent phase/analysis decisions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ralph.display.panels.analysis import _DECISION_TO_SEMANTIC
from ralph.display.theme import RALPH_THEME, format_status

if TYPE_CHECKING:
    from rich.theme import Theme

    from ralph.display.snapshot import DashboardSnapshot


_MAX_ROWS = 6
_MAX_REASON_CHARS = 60
_SECONDS_PER_MINUTE = 60
_MINUTES_PER_HOUR = 60
_HOURS_PER_DAY = 24


def _format_relative_delta(seconds: int) -> str:
    if seconds < 0:
        return "now"
    if seconds < _SECONDS_PER_MINUTE:
        return f"{seconds}s ago"
    minutes = seconds // _SECONDS_PER_MINUTE
    if minutes < _MINUTES_PER_HOUR:
        return f"{minutes}m ago"
    hours = minutes // _MINUTES_PER_HOUR
    if hours < _HOURS_PER_DAY:
        return f"{hours}h ago"
    days = hours // _HOURS_PER_DAY
    return f"{days}d ago"


def _relative_time(iso_ts: str | None) -> str:
    if not iso_ts:
        return ""
    try:
        ts = datetime.fromisoformat(iso_ts)
    except ValueError:
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - ts
    return _format_relative_delta(int(delta.total_seconds()))


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


class DecisionLogPanel:
    name = "decision_log"

    def render(
        self,
        snapshot: DashboardSnapshot,
        *,
        theme: Theme = RALPH_THEME,
        width: int | None = None,
    ) -> Panel:
        del width
        border = theme.styles.get("theme.panel.border", "")

        if not snapshot.decision_log:
            return Panel(
                Text.from_markup("[dim]no decisions yet[/dim]"),
                title="Decision Log",
                border_style=border,
                padding=(0, 1),
            )

        rows = snapshot.decision_log[-_MAX_ROWS:]
        table = Table(show_header=True, box=None, header_style="bold")
        table.add_column("Phase", overflow="fold", max_width=20)
        table.add_column("Decision")
        table.add_column("Reason", overflow="fold")
        table.add_column("When", justify="right")

        for entry in rows:
            phase, decision, reason, iso_ts = entry
            decision_key = decision.lower()
            semantic = _DECISION_TO_SEMANTIC.get(decision_key, "info")
            badge = format_status(semantic)
            decision_cell = Text.from_markup(
                f"{badge} {escape(decision)}"
            )
            phase_cell = Text(phase.replace("_", " ").title())
            reason_text = _truncate(reason or "", _MAX_REASON_CHARS)
            reason_cell = Text(reason_text, style="dim")
            time_cell = Text(_relative_time(iso_ts), style="dim")
            table.add_row(phase_cell, decision_cell, reason_cell, time_cell)

        return Panel(
            table,
            title="Decision Log",
            border_style=border,
            padding=(0, 1),
        )


decision_log_panel = DecisionLogPanel()
