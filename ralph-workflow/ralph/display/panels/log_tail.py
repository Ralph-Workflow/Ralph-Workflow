"""Log tail panel showing per-worker RingBuffer activity."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from ralph.display.theme import RALPH_THEME, format_status

if TYPE_CHECKING:
    from collections.abc import Mapping

    from rich.theme import Theme

    from ralph.display.ring_buffer import RingBuffer
    from ralph.display.snapshot import DashboardSnapshot

TAIL_N = 8
MAX_WORKERS_SHOWN = 3


class LogTailPanel:
    name = "log_tail"

    def __init__(self, buffers: Mapping[str, RingBuffer]) -> None:
        self._buffers = buffers

    def render(
        self,
        snapshot: DashboardSnapshot,
        *,
        theme: Theme = RALPH_THEME,
        width: int | None = None,
    ) -> Panel:
        workers = snapshot.workers
        if not workers:
            return Panel(
                Text.from_markup("[dim]no logs yet[/dim]"),
                title="Agent Activity",
                border_style=theme.styles.get("theme.panel.border", ""),
            )

        sub_panels: list[Panel] = []
        for worker in workers[:MAX_WORKERS_SHOWN]:
            unit_id = worker.unit_id
            buffer = self._buffers.get(unit_id)

            if buffer is None:
                lines: list[str] = []
            else:
                lines = buffer.snapshot(TAIL_N)

            dropped = buffer.dropped_count if buffer else 0
            title = (
                f"[theme.panel.title]{escape(unit_id)}[/] · "
            )
            title += format_status(worker.status_semantic)
            if dropped > 0:
                title += f" [theme.text.muted](dropped: {dropped})[/]"

            # Lines from RingBuffer are already pre-rendered Rich-markup strings.
            content = (
                Text.from_markup("\n".join(lines))
                if lines
                else Text.from_markup("[dim]waiting for output[/dim]")
            )

            sub_panels.append(
                Panel(
                    content,
                    title=title,
                    border_style=theme.styles.get("theme.panel.border", ""),
                    padding=(0, 1),
                ),
            )

        return Panel(
            Group(*sub_panels),
            title="Agent Activity",
            border_style=theme.styles.get("theme.panel.border", ""),
        )


def make_log_tail_panel(buffers: Mapping[str, RingBuffer]) -> LogTailPanel:
    """Factory to build a LogTailPanel with the given per-worker buffers."""
    return LogTailPanel(buffers)
