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


def test_emit_phase_transition_propagates_artifact_outcome_from_display() -> None:
    """Exit model should carry artifact_outcome from display's last_phase_artifact_outcome."""
    display = _StubDisplay()
    display.last_phase_artifact_outcome = "plan: 3 step(s), 2 risk(s)"
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
    assert exit_model.artifact_outcome == "plan: 3 step(s), 2 risk(s)"


def test_emit_phase_transition_uses_produced_exit_trigger_when_artifact_present() -> None:
    """When last_phase_artifact_outcome is non-empty, exit_trigger should be 'produced'."""
    display = _StubDisplay()
    display.last_phase_artifact_outcome = "plan: 5 step(s), 2 risk(s)"
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
    assert exit_model.artifact_outcome == "plan: 5 step(s), 2 risk(s)"
    assert exit_model.exit_trigger == "produced"


def test_emit_phase_transition_uses_completed_exit_trigger_without_artifact() -> None:
    """When last_phase_artifact_outcome is empty, exit_trigger should be 'completed'."""
    display = _StubDisplay()
    # Do not set last_phase_artifact_outcome (not in _StubDisplay) so it returns None/empty
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
    assert exit_model.exit_trigger == "completed"


def test_execute_commit_effect_uses_canonical_phase_name() -> None:
    """Commit close banner must use the canonical phase name, not the hardcoded 'commit' string."""
    import tempfile  # noqa: PLC0415
    import types  # noqa: PLC0415

    from ralph.pipeline.effects import CommitEffect  # noqa: PLC0415

    captured: dict[str, PhaseExitModel] = {}

    def _capture_close(exit_model: PhaseExitModel) -> None:
        captured["exit_model"] = exit_model

    # Build a minimal display stub with emit_phase_close_from_exit so the runner's
    # hasattr guard passes and we can capture the PhaseExitModel it builds.
    display = types.SimpleNamespace(
        emit_phase_close_from_exit=_capture_close,
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("feat: add canonical phase name\n")
        msg_path = f.name

    effect = CommitEffect(message_file=msg_path)

    def _fake_create_commit(repo_root: str, message: str) -> str:
        del repo_root, message
        return "abc1234567890"

    def _fake_stage_all(repo_root: str) -> None:
        del repo_root

    with (
        patch("ralph.pipeline.runner.render_commit_message"),
        patch("ralph.pipeline.runner._repo_has_commit_work", return_value=True),
    ):
        runner_module._execute_commit_effect(
            effect,
            _fake_create_commit,
            _fake_stage_all,
            Path("/tmp"),
            cast("runner_module.ParallelDisplay | runner_module._LegacyConsoleDisplay", display),
            verbosity=runner_module.Verbosity.VERBOSE,
            phase_name="development_commit",
        )

    assert "exit_model" in captured, "emit_phase_close_from_exit was not called"
    assert captured["exit_model"].phase_name == "development_commit"
    assert captured["exit_model"].exit_trigger == "produced"


def test_execute_commit_effect_carries_iteration_context_from_state() -> None:
    """Commit close banner must carry outer dev iteration context when state/policy given."""
    import tempfile  # noqa: PLC0415
    import types  # noqa: PLC0415

    from ralph.pipeline.effects import CommitEffect  # noqa: PLC0415
    from ralph.pipeline.state import PipelineState  # noqa: PLC0415
    from ralph.policy.models import (  # noqa: PLC0415
        BudgetCounterConfig,
        PhaseCommitPolicy,
        PhaseDefinition,
        PhaseTransition,
        PipelinePolicy,
        RecoveryPolicy,
    )

    captured: dict[str, PhaseExitModel] = {}

    def _capture_close(exit_model: PhaseExitModel) -> None:
        captured["exit_model"] = exit_model

    display = types.SimpleNamespace(
        emit_phase_close_from_exit=_capture_close,
    )

    # Build a policy with a commit phase that increments the 'iteration' counter
    policy = PipelinePolicy(
        entry_phase="development",
        terminal_phase="done",
        phases={
            "development": PhaseDefinition(
                drain="development",
                role="execution",
                transitions=PhaseTransition(on_success="development_commit"),
            ),
            "development_commit": PhaseDefinition(
                drain="development_commit",
                role="commit",
                commit_policy=PhaseCommitPolicy(increments_counter="iteration"),
                transitions=PhaseTransition(on_success="done"),
            ),
            "done": PhaseDefinition(
                drain="development",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done"),
            ),
            "failed_terminal": PhaseDefinition(
                drain="development",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
        },
        budget_counters={"iteration": BudgetCounterConfig(tracks_budget=True, default_max=4)},
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )
    # State: on second iteration (outer_progress=1 means iteration #2 is about to start)
    state = PipelineState(
        phase="development_commit",
        outer_progress={"iteration": 1},
        budget_caps={"iteration": 4},
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("feat: add iteration context\n")
        msg_path = f.name

    effect = CommitEffect(message_file=msg_path)

    def _fake_create_commit(repo_root: str, message: str) -> str:
        del repo_root, message
        return "def4567890ab"

    def _fake_stage_all(repo_root: str) -> None:
        del repo_root

    with (
        patch("ralph.pipeline.runner.render_commit_message"),
        patch("ralph.pipeline.runner._repo_has_commit_work", return_value=True),
    ):
        runner_module._execute_commit_effect(
            effect,
            _fake_create_commit,
            _fake_stage_all,
            Path("/tmp"),
            cast("runner_module.ParallelDisplay | runner_module._LegacyConsoleDisplay", display),
            verbosity=runner_module.Verbosity.VERBOSE,
            phase_name="development_commit",
            state=state,
            pipeline_policy=policy,
        )

    assert "exit_model" in captured, "emit_phase_close_from_exit was not called"
    exit_model = captured["exit_model"]
    assert exit_model.phase_name == "development_commit"
    assert exit_model.exit_trigger == "produced"
    # Iteration context must be populated from state
    _expected_iteration = 2
    _expected_cap = 4
    assert exit_model.outer_dev_iteration == _expected_iteration, (
        f"Expected outer_dev_iteration={_expected_iteration} (iteration 1+1),"
        f" got {exit_model.outer_dev_iteration}"
    )
    assert exit_model.outer_dev_cap == _expected_cap, (
        f"Expected outer_dev_cap={_expected_cap}, got {exit_model.outer_dev_cap}"
    )
