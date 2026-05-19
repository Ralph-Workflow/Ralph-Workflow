"""State subscriber adapter for the plain log renderer."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ralph.display.plain_renderer._plain_log_renderer import PlainLogRenderer
from ralph.display.snapshot import snapshot_from_state

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.display.context import DisplayContext
    from ralph.pipeline.state import PipelineState


class PlainModeAdapter:
    """State subscriber that projects pipeline state to plain log lines."""

    def __init__(
        self,
        display_context: DisplayContext,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._renderer = PlainLogRenderer(display_context, clock=clock)

    def notify(self, state: PipelineState) -> None:
        self._renderer.emit_snapshot(snapshot_from_state(state))
