"""Regression tests for runner-driven rich phase-close banners."""

from __future__ import annotations

import tempfile
import types
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import CommitEffect
from ralph.pipeline.state import PipelineState
from ralph.pipeline.state import PipelineState as ReviewState
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    BudgetCounterConfig,
    PhaseCommitPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    RecoveryPolicy,
)

if TYPE_CHECKING:
    from ralph.display.phase_lifecycle import PhaseExitModel

_DEFAULT_POLICY = load_policy(Path(__file__).parent.parent / "ralph" / "policy" / "defaults")
_EXPECTED_ELAPSED_SECONDS = 12.5
_STUB_CONTENT_BLOCKS = 5
_STUB_THINKING_BLOCKS = 3
_STUB_TOOL_CALLS = 7
_STUB_ERRORS = 1


class _StubDisplay:

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
        # Record that close was emitted (for phase_close_emitted flag)
        self._phase_close_emitted = True
        self._last_exit_model = exit_model


_StubPhaseCounters = _StubDisplay._StubPhaseCounters
_StubSubscriber = _StubDisplay._StubSubscriber


def test_emit_phase_transition_populates_close_banner_exit_trigger() -> None:
    """emit_phase_close_from_exit should be called with exit_trigger='completed'."""
    display = _StubDisplay()
    state = PipelineState(
        phase="planning_analysis",
        previous_phase="planning",
        budget_caps={"iteration": 1},
    )

    result = runner_module.emit_phase_transition_if_changed(
        cast("runner_module.ParallelDisplay | runner_module.LegacyConsoleDisplay", display),
        "planning",
        state,
        verbosity=runner_module.Verbosity.VERBOSE,
        pipeline_policy=_DEFAULT_POLICY.pipeline,
    )

    assert result == "planning_analysis"
    exit_model = display._last_exit_model
    assert exit_model is not None
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

    runner_module.emit_phase_transition_if_changed(
        cast("runner_module.ParallelDisplay | runner_module.LegacyConsoleDisplay", display),
        "planning",
        state,
        verbosity=runner_module.Verbosity.VERBOSE,
        pipeline_policy=_DEFAULT_POLICY.pipeline,
    )

    exit_model = display._last_exit_model
    assert exit_model is not None
    assert exit_model.last_failure_category == "timeout"


def test_emit_phase_transition_populates_waiting_status_from_subscriber() -> None:
    """Exit model should carry waiting_status_line from display subscriber."""
    display = _StubDisplay()
    state = PipelineState(
        phase="planning_analysis",
        previous_phase="planning",
        budget_caps={"iteration": 1},
    )

    runner_module.emit_phase_transition_if_changed(
        cast("runner_module.ParallelDisplay | runner_module.LegacyConsoleDisplay", display),
        "planning",
        state,
        verbosity=runner_module.Verbosity.VERBOSE,
        pipeline_policy=_DEFAULT_POLICY.pipeline,
    )

    exit_model = display._last_exit_model
    assert exit_model is not None
    assert exit_model.waiting_status_line is None  # _StubSubscriber returns None


def test_emit_phase_transition_populates_activity_counters_from_display() -> None:
    """Exit model should carry activity counters from display's last_phase_counters."""
    display = _StubDisplay()
    state = PipelineState(
        phase="planning_analysis",
        previous_phase="planning",
        budget_caps={"iteration": 1},
    )

    result = runner_module.emit_phase_transition_if_changed(
        cast("runner_module.ParallelDisplay | runner_module.LegacyConsoleDisplay", display),
        "planning",
        state,
        verbosity=runner_module.Verbosity.VERBOSE,
        pipeline_policy=_DEFAULT_POLICY.pipeline,
    )

    assert result == "planning_analysis"
    exit_model = display._last_exit_model
    assert exit_model is not None
    assert exit_model.content_blocks == _STUB_CONTENT_BLOCKS
    assert exit_model.thinking_blocks == _STUB_THINKING_BLOCKS
    assert exit_model.tool_calls == _STUB_TOOL_CALLS
    assert exit_model.errors == _STUB_ERRORS


def test_emit_phase_transition_propagates_artifact_outcome_from_display() -> None:
    """Exit model should carry artifact_outcome from display's last_phase_artifact_outcome."""
    display = _StubDisplay()
    display._last_phase_artifact_outcome = "plan: 3 step(s), 2 risk(s)"
    state = PipelineState(
        phase="planning_analysis",
        previous_phase="planning",
        budget_caps={"iteration": 1},
    )

    runner_module.emit_phase_transition_if_changed(
        cast("runner_module.ParallelDisplay | runner_module.LegacyConsoleDisplay", display),
        "planning",
        state,
        verbosity=runner_module.Verbosity.VERBOSE,
        pipeline_policy=_DEFAULT_POLICY.pipeline,
    )

    exit_model = display._last_exit_model
    assert exit_model is not None
    assert exit_model.artifact_outcome == "plan: 3 step(s), 2 risk(s)"


def test_emit_phase_transition_uses_produced_exit_trigger_when_artifact_present() -> None:
    """When last_phase_artifact_outcome is non-empty, exit_trigger should be 'produced'."""
    display = _StubDisplay()
    display._last_phase_artifact_outcome = "plan: 5 step(s), 2 risk(s)"
    state = PipelineState(
        phase="planning_analysis",
        previous_phase="planning",
        budget_caps={"iteration": 1},
    )

    runner_module.emit_phase_transition_if_changed(
        cast("runner_module.ParallelDisplay | runner_module.LegacyConsoleDisplay", display),
        "planning",
        state,
        verbosity=runner_module.Verbosity.VERBOSE,
        pipeline_policy=_DEFAULT_POLICY.pipeline,
    )

    exit_model = display._last_exit_model
    assert exit_model is not None
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

    runner_module.emit_phase_transition_if_changed(
        cast("runner_module.ParallelDisplay | runner_module.LegacyConsoleDisplay", display),
        "planning",
        state,
        verbosity=runner_module.Verbosity.VERBOSE,
        pipeline_policy=_DEFAULT_POLICY.pipeline,
    )

    exit_model = display._last_exit_model
    assert exit_model is not None
    assert exit_model.exit_trigger == "completed"


def test_execute_commit_effect_records_sha_artifact_outcome() -> None:
    """Commit effect must record the sha as artifact outcome for the phase-close banner."""


    recorded: dict[str, str] = {}

    def _capture_outcome(outcome: str) -> None:
        recorded["outcome"] = outcome

    display = types.SimpleNamespace(
        record_artifact_outcome=_capture_outcome,
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
        patch("ralph.pipeline.runner.repo_has_commit_work", return_value=True),
    ):
        runner_module.execute_commit_effect(
            effect,
            _fake_create_commit,
            _fake_stage_all,
            Path("/tmp"),
            cast("runner_module.ParallelDisplay | runner_module.LegacyConsoleDisplay", display),
            verbosity=runner_module.Verbosity.VERBOSE,
            phase_name="development_commit",
        )

    assert "outcome" in recorded, "record_artifact_outcome was not called"
    assert recorded["outcome"].startswith("sha="), (
        f"Expected sha=<short-sha>, got: {recorded['outcome']}"
    )


def test_execute_commit_effect_records_sha_regardless_of_state() -> None:
    """Commit effect records sha artifact outcome even when state and policy are provided."""


    recorded: dict[str, str] = {}

    def _capture_outcome(outcome: str) -> None:
        recorded["outcome"] = outcome

    display = types.SimpleNamespace(
        record_artifact_outcome=_capture_outcome,
    )

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
        patch("ralph.pipeline.runner.repo_has_commit_work", return_value=True),
    ):
        runner_module.execute_commit_effect(
            effect,
            _fake_create_commit,
            _fake_stage_all,
            Path("/tmp"),
            cast("runner_module.ParallelDisplay | runner_module.LegacyConsoleDisplay", display),
            verbosity=runner_module.Verbosity.VERBOSE,
            phase_name="development_commit",
            state=state,
            pipeline_policy=policy,
        )

    assert "outcome" in recorded, "record_artifact_outcome was not called"
    assert recorded["outcome"].startswith("sha="), (
        f"Expected sha=<short-sha>, got: {recorded['outcome']}"
    )


def test_emit_phase_transition_shows_rich_transition_banner_for_major_routing() -> None:
    """Major phase transitions should render the dedicated transition banner.

    The runtime already emits the close banner for the phase being left and the
    next phase later emits its own start banner. This test locks in the missing
    middle surface: the rich transition separator that shows the actual phase
    handoff (for example Planning → Planning Analysis).
    """
    display = _StubDisplay()
    state = PipelineState(
        phase="planning_analysis",
        previous_phase="planning",
        budget_caps={"iteration": 1},
    )

    runner_module.emit_phase_transition_if_changed(
        cast("runner_module.ParallelDisplay | runner_module.LegacyConsoleDisplay", display),
        "planning",
        state,
        verbosity=runner_module.Verbosity.VERBOSE,
        pipeline_policy=_DEFAULT_POLICY.pipeline,
    )

    # The close banner still emits for the phase being left.
    assert display._phase_close_emitted, "emit_phase_close_from_exit must be called"
    exit_model = display._last_exit_model
    assert exit_model is not None

    output = display._ctx.console.export_text()
    arrow = display._ctx.glyph_for("arrow")
    assert f"Planning {arrow} Planning Analysis" in output, output


def test_emit_phase_transition_calls_canonical_rich_phase_change_surfaces() -> None:
    """Runner phase changes must emit both the close and transition rich surfaces.

    This locks the runner contract instead of only testing the individual
    renderers in isolation: if a refactor drops either surface from the live
    path, this test should fail immediately.
    """
    display = _StubDisplay()
    state = PipelineState(
        phase="planning_analysis",
        previous_phase="planning",
        budget_caps={"iteration": 1},
    )

    with (
        patch("ralph.pipeline.runner.show_phase_close_banner") as mock_close,
        patch("ralph.pipeline.runner.show_phase_transition") as mock_transition,
    ):
        runner_module.emit_phase_transition_if_changed(
            cast("runner_module.ParallelDisplay | runner_module.LegacyConsoleDisplay", display),
            "planning",
            state,
            verbosity=runner_module.Verbosity.VERBOSE,
            pipeline_policy=_DEFAULT_POLICY.pipeline,
        )

    mock_close.assert_called_once()
    close_call = mock_close.call_args
    assert close_call is not None
    exit_model_arg = close_call.args[0] if close_call.args else close_call.kwargs.get("exit_model")
    assert exit_model_arg is not None, "show_phase_close_banner must receive exit_model"

    mock_transition.assert_called_once_with(
        "planning",
        "planning_analysis",
        context=None,
        display_context=display._ctx,
        pipeline_policy=_DEFAULT_POLICY.pipeline,
    )


def test_emit_phase_transition_skipped_analysis_emits_routing_note() -> None:
    """When analysis is skipped due to cap exhaustion, a brief routing note is printed.

    Instead of emitting a full transition banner that would duplicate the close banner's
    information, we emit a brief single-line routing note directly to console.
    The close banner already communicated iteration context and exit_trigger, so
    we only note *why* the routing happened for debugging clarity.
    """
    # To trigger skipped analysis, we need:
    # - previous_phase role is "execution" or "review"
    # - on_success target is an analysis phase with exhausted loop counter
    #
    # In the default policy, development (role=execution) has on_success=development_analysis.
    # When development_analysis_iteration hits cap, we skip development_analysis and go
    # directly to development_commit. So we set state.phase="development_commit" (the
    # actual phase we transitioned to) which differs from previous_phase="development".
    display = _StubDisplay()
    state = PipelineState(
        phase="development_commit",  # Actual phase after skipping analysis
        previous_phase="development",  # Previous phase (execution role)
        budget_caps={"iteration": 1},
        # Set loop iteration to cap to trigger skipped analysis
        loop_iterations={"development_analysis_iteration": 5},
        loop_caps={"development_analysis_iteration": 5},
    )

    routing_notes: list[str] = []

    def _capture_print(routing_line: object) -> None:
        # Capture the routing line that was printed
        routing_notes.append(str(routing_line))

    console_print_patch = patch("rich.console.Console.print", side_effect=_capture_print)
    with console_print_patch:
        runner_module.emit_phase_transition_if_changed(
            cast("runner_module.ParallelDisplay | runner_module.LegacyConsoleDisplay", display),
            "development",
            state,
            verbosity=runner_module.Verbosity.VERBOSE,
            pipeline_policy=_DEFAULT_POLICY.pipeline,
        )

    # Verify that among all printed lines, at least one is the routing note
    # explaining why analysis was skipped. (Other lines come from the rich
    # close banner which is also printed via the same Console.print path.)
    assert any("skipped" in note.lower() or "cap" in note.lower() for note in routing_notes), (
        f"No routing note about skipped/cap found among prints: {routing_notes}"
    )


def test_emit_phase_transition_review_issues_found_set_for_review_phase() -> None:
    """review_issues_found must be populated when transitioning from a review phase."""

    policy = PipelinePolicy(
        entry_phase="review",
        terminal_phase="done",
        phases={
            "review": PhaseDefinition(
                drain="review",
                role="review",
                clean_outcome="clean",
                transitions=PhaseTransition(on_success="done"),
            ),
            "done": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done", on_loopback="done"),
            ),
            "failed_terminal": PhaseDefinition(
                drain="failed_terminal",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(
                    on_success="failed_terminal", on_loopback="failed_terminal"
                ),
            ),
        },
        budget_counters={"iteration": BudgetCounterConfig(tracks_budget=True, default_max=4)},
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )

    display = _StubDisplay()
    # State with review_outcome set to issues-found (not "clean")

    state = ReviewState(
        phase="done",
        previous_phase="review",
        review_outcome="issues",
    )

    runner_module.emit_phase_transition_if_changed(
        cast("runner_module.ParallelDisplay | runner_module.LegacyConsoleDisplay", display),
        "review",
        state,
        verbosity=runner_module.Verbosity.VERBOSE,
        pipeline_policy=policy,
    )

    exit_model = display._last_exit_model
    assert exit_model is not None
    assert exit_model.review_issues_found is True, (
        "review_issues_found must be True when transitioning from a review phase with issues"
    )


def test_emit_phase_transition_shows_skip_and_changes_for_capped_loopback(
) -> None:
    """The live runner transition banner must show the capped planning-analysis loopback context."""
    display = _StubDisplay()
    state = PipelineState(
        phase="planning",
        previous_phase="planning_analysis",
        loop_iterations={"planning_analysis_iteration": 3},
        loop_caps={"planning_analysis_iteration": 3},
    )

    setattr(
        display,
        runner_module.PENDING_PHASE_TRANSITION_METADATA_ATTR,
        runner_module.PendingPhaseTransitionMetadata(
            previous_phase="planning_analysis",
            current_phase="planning",
            transition_context={
                "analysis_status": "final, skipping next",
                "decision": "request changes",
            },
        ),
    )

    runner_module.emit_phase_transition_if_changed(
        cast("runner_module.ParallelDisplay | runner_module.LegacyConsoleDisplay", display),
        "planning_analysis",
        state,
        verbosity=runner_module.Verbosity.VERBOSE,
        pipeline_policy=_DEFAULT_POLICY.pipeline,
    )

    output = display._ctx.console.export_text()
    arrow = display._ctx.glyph_for("arrow")
    assert "final, skipping next" in output
    assert f"{arrow} request changes" in output


def test_emit_phase_transition_review_issues_found_none_for_non_review_phase() -> None:
    """review_issues_found must be None when transitioning from a non-review phase."""
    display = _StubDisplay()
    state = PipelineState(
        phase="planning_analysis",
        previous_phase="planning",
        budget_caps={"iteration": 1},
        review_outcome="issues",  # Set but should be ignored for non-review phases
    )

    runner_module.emit_phase_transition_if_changed(
        cast("runner_module.ParallelDisplay | runner_module.LegacyConsoleDisplay", display),
        "planning",
        state,
        verbosity=runner_module.Verbosity.VERBOSE,
        pipeline_policy=_DEFAULT_POLICY.pipeline,
    )

    exit_model = display._last_exit_model
    assert exit_model is not None
    assert exit_model.review_issues_found is None, (
        "review_issues_found must be None when transitioning from a non-review phase"
    )


def test_emit_phase_transition_shows_bypass_metadata_without_fake_decision() -> None:
    display = _StubDisplay()
    state = PipelineState(
        phase="development",
        previous_phase="planning",
        loop_iterations={"planning_analysis_iteration": 3},
        loop_caps={"planning_analysis_iteration": 3},
    )
    setattr(
        display,
        runner_module.PENDING_PHASE_TRANSITION_METADATA_ATTR,
        runner_module.PendingPhaseTransitionMetadata(
            previous_phase="planning",
            current_phase="development",
            transition_context={"Planning Analysis": "cap reached, skipping"},
            routing_note="Planning Analysis cap reached, skipping",
        ),
    )

    runner_module.emit_phase_transition_if_changed(
        cast("runner_module.ParallelDisplay | runner_module.LegacyConsoleDisplay", display),
        "planning",
        state,
        verbosity=runner_module.Verbosity.VERBOSE,
        pipeline_policy=_DEFAULT_POLICY.pipeline,
    )

    output = display._ctx.console.export_text()
    arrow = display._ctx.glyph_for("arrow")
    assert "Planning Analysis cap reached, skipping" in output
    assert f"{arrow} request changes" not in output
