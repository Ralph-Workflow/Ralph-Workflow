"""Footer panel with keyboard hints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markup import escape
from rich.text import Text

from ralph.display.theme import RALPH_THEME

if TYPE_CHECKING:
    from rich.theme import Theme

    from ralph.display.snapshot import DashboardSnapshot

TERMINAL_PHASES = {"complete", "failed", "interrupted"}
_MIN_WIDTH_FOR_FULL_HINTS = 80


class FooterPanel:
    name = "footer"

    def render(
        self,
        snapshot: DashboardSnapshot,
        *,
        theme: Theme = RALPH_THEME,
        width: int | None = None,
    ) -> Text:
        muted_style = theme.styles.get("theme.text.muted", "")

        if width is not None and width < _MIN_WIDTH_FOR_FULL_HINTS:
            return Text("Ctrl+C: interrupt", style=muted_style)

        if snapshot.phase in TERMINAL_PHASES and snapshot.run_id:
            run_id = escape(snapshot.run_id)
            return Text(
                f"Press Ctrl+C to dismiss · log: ~/.agent/logs/{run_id}/",
                style=muted_style,
            )

        return Text(
            "Ctrl+C: interrupt · NO_COLOR=1 to disable colors · CI=1 for plain output",
            style=muted_style,
        )


footer_panel = FooterPanel()
