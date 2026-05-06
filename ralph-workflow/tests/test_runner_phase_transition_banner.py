"""Regression tests for runner-driven rich phase-close banners."""

from __future__ import annotations

from dataclasses import dataclass
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
_STUB_CONTENT_BLOCKS = 5
_STUB_THINKING_BLOCKS = 3
_STUB_TOOL_CALLS = 7
_STUB_ERRORS = 1


@dataclass
class _StubPhaseCounters:
    content_blocks: int = 0
    thinking_blocks: int = 0
    tool_calls: int = 0
    errors: int = 0


class _StubSubscriber:
    """Minimal subscriber stub — only waiting_status_line is needed."""

    @property
    def waiting_status_line(self) -> str | None:
        return None


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


def test_emit_phase_transition_populates_close_banner_exit_trigger() -> None:
    """Rich phase-close banner should include an explicit exit trigger for completed phases."""
    display = _StubDisplay()
    state = PipelineState(
        phase="planning_analysis",
        previous_phase="planning",
        budget_caps={"iteration": 1},
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


def test_emit_phase_transition_populates_last_failure_category_from_state() -> None:
    """Exit model should carry last_failure_category from pipeline state."""
    display = _StubDisplay()
    state = PipelineState(
        phase="planning_analysis",
        previous_phase="planning",
        budget_caps={"iteration": 1},
        last_failure_category="timeout",
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
        runner_module._emit_phase_transition_if_changed(
            cast("runner_module.ParallelDisplay | runner_module._LegacyConsoleDisplay", display),
            "planning",
            state,
            verbosity=runner_module.Verbosity.VERBOSE,
            pipeline_policy=_DEFAULT_POLICY.pipeline,
        )

    exit_model = captured["exit_model"]
    assert exit_model.last_failure_category == "timeout"


def test_emit_phase_transition_populates_waiting_status_from_subscriber() -> None:
    """Exit model should carry waiting_status_line from display subscriber."""
    import queue  # noqa: PLC0415

    from ralph.display.parallel_display import ParallelDisplay  # noqa: PLC0415
    from ralph.display.subscriber import PipelineSubscriber  # noqa: PLC0415

    q: queue.Queue = queue.Queue(maxsize=64)
    buf_console = Console(record=True, force_terminal=False, width=120, color_system=None)
    ctx = make_display_context(console=buf_console, env={})
    subscriber = PipelineSubscriber(
        queue=q,
        workspace_root=Path("/tmp"),
        run_id="test-run",
    )
    # Manually set the waiting status line via the property
    subscriber._waiting_status_line = "waiting for child process"

    display = ParallelDisplay(ctx, subscriber=subscriber)
    state = PipelineState(
        phase="planning_analysis",
        previous_phase="planning",
        budget_caps={"iteration": 1},
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
        runner_module._emit_phase_transition_if_changed(
            cast("runner_module.ParallelDisplay | runner_module._LegacyConsoleDisplay", display),
            "planning",
            state,
            verbosity=runner_module.Verbosity.VERBOSE,
            pipeline_policy=_DEFAULT_POLICY.pipeline,
        )

    exit_model = captured["exit_model"]
    assert exit_model.waiting_status_line == "waiting for child process"


def test_emit_phase_transition_populates_activity_counters_from_display() -> None:
    """Exit model should carry activity counters from display's last_phase_counters."""
    display = _StubDisplay()
    state = PipelineState(
        phase="planning_analysis",
        previous_phase="planning",
        budget_caps={"iteration": 1},
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
    assert exit_model.content_blocks == _STUB_CONTENT_BLOCKS
    assert exit_model.thinking_blocks == _STUB_THINKING_BLOCKS
    assert exit_model.tool_calls == _STUB_TOOL_CALLS
    assert exit_model.errors == _STUB_ERRORS
