"""Plan panel showing the active plan summary, scope items, and prompt preview."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from ralph.display.theme import RALPH_THEME

if TYPE_CHECKING:
    from rich.theme import Theme

    from ralph.display.snapshot import DashboardSnapshot

_MAX_SCOPE_ITEMS = 6
_MAX_SCOPE_ITEM_CHARS = 90
_MAX_SUMMARY_CHARS = 240


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


class PlanPanel:
    name = "plan"

    def render(
        self,
        snapshot: DashboardSnapshot,
        *,
        theme: Theme = RALPH_THEME,
        width: int | None = None,
    ) -> Panel:
        del width
        title = (
            f"Plan · {escape(snapshot.prompt_path)}" if snapshot.prompt_path else "Plan"
        )
        border = theme.styles.get("theme.panel.border", "")

        lines: list[str] = []
        has_plan = snapshot.plan_summary is not None or bool(snapshot.plan_scope_items)
        if has_plan:
            if snapshot.plan_summary:
                summary = _truncate(snapshot.plan_summary, _MAX_SUMMARY_CHARS)
                lines.append(f"[bold]Context:[/] {escape(summary)}")
            if snapshot.plan_scope_items:
                shown = snapshot.plan_scope_items[:_MAX_SCOPE_ITEMS]
                lines.extend(
                    f"  • {escape(_truncate(item, _MAX_SCOPE_ITEM_CHARS))}" for item in shown
                )
                remaining = len(snapshot.plan_scope_items) - len(shown)
                if remaining > 0:
                    lines.append(f"  [dim]… +{remaining} more[/dim]")
            if snapshot.plan_total_steps > 0:
                current = (
                    str(snapshot.plan_current_step)
                    if snapshot.plan_current_step is not None
                    else "—"
                )
                lines.append(
                    f"[dim]Steps: {current}/{snapshot.plan_total_steps}[/dim]"
                )
        elif snapshot.prompt_preview:
            joined = "\n".join(snapshot.prompt_preview)
            lines.append(joined)
        else:
            lines.append("[dim]no plan attached[/dim]")

        return Panel(
            Text.from_markup("\n".join(lines)),
            title=title,
            border_style=border,
            padding=(0, 1),
        )


plan_panel = PlanPanel()
