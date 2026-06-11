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
        self.close_banner_called = 0
        self.transition_called = 0

    @property
    def phase_close_emitted(self) -> bool:
        return self._phase_close_emitted

    @property
    def last_phase_artifact_outcome(self) -> str | None:
        return self._last_phase_artifact_outcome

    def emit_phase_close_from_exit(self, exit_model: PhaseExitModel) -> None:
        self._phase_close_emitted = True
        self._last_exit_model = exit_model

    def emit_phase_close_banner(
        self, exit_model: PhaseExitModel, *, pipeline_policy: object = None
    ) -> None:
        self.close_banner_called += 1
        self._last_exit_model = exit_model
        self._ctx.console.print(f"[close-banner:{exit_model.phase_name}]")

    def emit_phase_transition(
        self,
        previous_phase: str,
        current_phase: str,
        *,
        context: object = None,
        pipeline_policy: object = None,
    ) -> None:
        self.transition_called += 1
        ctx_obj = dict(context) if isinstance(context, dict) else {}
        note_parts: list[str] = []
        for key, value in ctx_obj.items():
            if key == "decision":
                arrow = self._ctx.glyph_for("arrow")
                note_parts.append(f"{arrow} {value}")
            else:
                note_parts.append(f"{key}={value}")
        suffix = f" ({'; '.join(note_parts)})" if note_parts else ""
        arrow = self._ctx.glyph_for("arrow")
        prev_label = previous_phase.replace("_", " ").title()
        curr_label = current_phase.replace("_", " ").title()
        self._ctx.console.print(f"{prev_label} {arrow} {curr_label}{suffix}")
        pending = getattr(self, "_pending_routing_note", None)
        if pending is not None:
            self._ctx.console.print(f"  routing-note: {pending}")
