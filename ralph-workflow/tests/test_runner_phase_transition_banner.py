"""Regression tests for runner-driven rich phase-close banners."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.pipeline import runner as runner_module
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from ralph.display.phase_lifecycle import PhaseExitModel

_DEFAULT_POLICY = load_policy(Path(__file__).parent.parent / "ralph" / "policy" / "defaults")
_EXPECTED_ELAPSED_SECONDS = 12.5


class _StubDisplay:
    def __init__(self) -> None:
        console = Console(record=True, force_terminal=False, width=120, color_system=None)
        self._ctx = make_display_context(console=console, env={})
        self.last_phase_elapsed_seconds = _EXPECTED_ELAPSED_SECONDS


def test_emit_phase_transition_populates_close_banner_exit_trigger() -> None:
    """Rich phase-close banner should include an explicit exit trigger for completed phases."""
    display = _StubDisplay()
    state = PipelineState(
        phase="planning_analysis",
        previous_phase="planning",
        budget_caps={"iteration": 1},
        budget_remaining={"iteration": 1},
    )

    captured: dict[str, PhaseExitModel] = {}

    def _capture_close(
        exit_model: PhaseExitModel, *, display_context: object, pipeline_policy: object
    ) -> None:
        del display_context, pipeline_policy
        captured["exit_model"] = exit_model

    with (
        patch("ralph.pipeline.runner.show_phase_close_banner", side_effect=_capture_close),
        patch("ralph.pipeline.runner.show_phase_transition"),
    ):
        result = runner_module._emit_phase_transition_if_changed(
            cast("runner_module.ParallelDisplay | runner_module._LegacyConsoleDisplay", display),
            "planning",
            state,
            verbosity=runner_module.Verbosity.VERBOSE,
            pipeline_policy=_DEFAULT_POLICY.pipeline,
        )

    assert result == "planning_analysis"
    exit_model = captured["exit_model"]
    assert exit_model.elapsed_seconds == _EXPECTED_ELAPSED_SECONDS
    assert exit_model.exit_trigger == "completed"
