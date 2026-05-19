from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console

from ralph.display.context import make_display_context
from tests.test_runner_phase_transition_banner_helper__stubphasecounters import _StubPhaseCounters
from tests.test_runner_phase_transition_banner_helper__stubsubscriber import _StubSubscriber

if TYPE_CHECKING:
    from ralph.display.phase_lifecycle import PhaseExitModel

_EXPECTED_ELAPSED_SECONDS = 12.5

_STUB_CONTENT_BLOCKS = 5

_STUB_THINKING_BLOCKS = 3

_STUB_TOOL_CALLS = 7

_STUB_ERRORS = 1


class _StubDisplay:
    def __init__(self) -> None:
        console = Console(record=True, force_terminal=False, width=120, color_system=None)
        self._ctx = make_display_context(console=console, env={})
        self.last_phase_elapsed_seconds = _EXPECTED_ELAPSED_SECONDS
        self.last_phase_counters = _StubPhaseCounters(
            content_blocks=_STUB_CONTENT_BLOCKS,
            thinking_blocks=_STUB_THINKING_BLOCKS,
            tool_calls=_STUB_TOOL_CALLS,
            errors=_STUB_ERRORS,
        )
        self.subscriber = _StubSubscriber()
        self._phase_close_emitted = False
        self._last_exit_model: PhaseExitModel | None = None
        self._last_phase_artifact_outcome: str | None = None

    @property
    def phase_close_emitted(self) -> bool:
        return self._phase_close_emitted

    @property
    def last_phase_artifact_outcome(self) -> str | None:
        return self._last_phase_artifact_outcome

    def emit_phase_close_from_exit(self, exit_model: PhaseExitModel) -> None:
        self._phase_close_emitted = True
        self._last_exit_model = exit_model
