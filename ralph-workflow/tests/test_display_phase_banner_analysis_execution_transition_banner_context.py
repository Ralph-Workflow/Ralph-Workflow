"""Tests for ralph/display/parallel_display.py phase transition display.

The free-function ``ralph.display.phase_banner`` module was deleted in
wt-007-consolidate-display. The phase transition / start / close banner
logic is now owned by ``ParallelDisplay`` instance methods; these tests
exercise that consolidated surface.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.context import DisplayContext, make_display_context
from ralph.display.parallel_display import (
    MAJOR_ROLE_PAIRS,
    ParallelDisplay,
    phase_label,
    phase_style,
)
from ralph.display.phase_lifecycle import PhaseEntryModel
from ralph.policy.models import (
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    RecoveryPolicy,
)


def _ctx_from_console(console: Console) -> DisplayContext:
    """Create a DisplayContext from a Console for testing."""
    return make_display_context(console=console)


def test_show_phase_transition_renders_styled_output() -> None:
    console = Console(record=True)
    pd = ParallelDisplay(_ctx_from_console(console))
    pd.emit_phase_transition("planning", "development")
    output = console.export_text()
    assert "Planning" in output
    assert "Development" in output


def test_show_phase_transition_minor_renders_rule() -> None:
    console = Console(record=True)
    pd = ParallelDisplay(_ctx_from_console(console))
    pd.emit_phase_transition("development", "development_analysis")
    output = console.export_text()
    assert "Development" in output
    assert "Development Analysis" in output


def test_phase_label_converts_underscore_names() -> None:
    assert phase_label("development_analysis") == "Development Analysis"
    assert phase_label("review_commit") == "Review Commit"
    assert phase_label("planning") == "Planning"


def test_phase_style_canonical_names_without_policy_return_muted() -> None:
    assert phase_style("planning") == "theme.text.muted"
    assert phase_style("development") == "theme.text.muted"
    assert phase_style("complete") == "theme.text.muted"
    assert phase_style("failed") == "theme.text.muted"


def test_phase_style_role_names_without_policy_return_correct_styles() -> None:
    assert phase_style("review") == "theme.phase.review"
    assert phase_style("fix") == "theme.phase.fix"
    assert phase_style("execution") == "theme.phase.development"
    assert phase_style("terminal") == "theme.phase.complete"


def test_show_phase_start_without_counters() -> None:
    console = Console(record=True)
    pd = ParallelDisplay(_ctx_from_console(console))
    pd.emit_phase_start("planning")
    output = console.export_text()
    assert "Planning" in output
    assert "\u25b6" in output


def test_show_phase_start_with_agent_name() -> None:
    console = Console(record=True)
    pd = ParallelDisplay(_ctx_from_console(console))
    pd.emit_phase_start("development", agent_name="claude")
    output = console.export_text()
    assert "Development" in output
    assert "claude" in output


def test_show_phase_transition_with_context() -> None:
    policy = _make_two_phase_policy("execution", "analysis", "planning", "analysis_phase")
    console = Console(record=True, width=120)
    pd = ParallelDisplay(_ctx_from_console(console))
    pd.emit_phase_transition(
        "planning",
        "analysis_phase",
        context={"iteration": "1/5"},
        pipeline_policy=policy,
    )
    output = console.export_text()
    assert "Planning" in output
    assert "Analysis Phase" in output
    assert "iteration=1/5" in output


# --- New tests for expanded transitions and descriptions ---


def test_major_transition_analysis_to_commit_with_policy() -> None:
    """Analysis \u2192 commit is a major transition showing routing labels only."""
    policy = _make_two_phase_policy(
        "analysis",
        "commit",
        "dev_analysis",
        "dev_commit",
    )
    console = Console(record=True, width=120)
    pd = ParallelDisplay(_ctx_from_console(console))
    pd.emit_phase_transition(
        "dev_analysis",
        "dev_commit",
        pipeline_policy=policy,
    )
    output = console.export_text()
    assert "Dev Analysis" in output
    assert "Dev Commit" in output
    assert "Analysis approved" not in output


def test_major_transition_analysis_loopback_with_policy() -> None:
    """Analysis loopback \u2192 execution is a major transition with routing labels only."""
    policy = _make_two_phase_policy("analysis", "execution", "check", "work")
    console = Console(record=True, width=120)
    pd = ParallelDisplay(_ctx_from_console(console))
    pd.emit_phase_transition(
        "check",
        "work",
        pipeline_policy=policy,
    )
    output = console.export_text()
    assert "Check" in output
    assert "Work" in output
    assert "Analysis requested changes" not in output


def test_major_transition_review_to_terminal_with_policy() -> None:
    """Review \u2192 terminal should be a major transition when policy provides roles."""
    terminal_name = "done"
    policy = PipelinePolicy(
        phases={
            "review_phase": PhaseDefinition(
                drain="review_phase",
                role="review",
                transitions=PhaseTransition(on_success=terminal_name),
            ),
            terminal_name: PhaseDefinition(
                drain=terminal_name,
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success=terminal_name),
            ),
        },
        entry_phase="review_phase",
        terminal_phase=terminal_name,
        recovery=RecoveryPolicy(failed_route=terminal_name),
    )
    console = Console(record=True, width=120)
    pd = ParallelDisplay(_ctx_from_console(console))
    pd.emit_phase_transition(
        "review_phase",
        terminal_name,
        pipeline_policy=policy,
    )
    output = console.export_text()
    assert "Review Phase" in output
    assert "Done" in output


def test_unknown_transition_renders_gracefully() -> None:
    """Unknown transition pair should still render without crashing."""
    console = Console(record=True, width=120)
    pd = ParallelDisplay(_ctx_from_console(console))
    pd.emit_phase_transition(
        "unknown_phase",
        "another_unknown",
    )
    output = console.export_text()
    assert "Unknown Phase" in output
    assert "Another Unknown" in output


def test_major_role_pairs_transition_shows_no_duplicated_description() -> None:
    """Major role-pair transitions must not show duplicated description prose."""
    for from_role, to_role in MAJOR_ROLE_PAIRS:
        policy = _make_two_phase_policy(from_role, to_role, "phase_a", "phase_b")
        console = Console(record=True, width=120)
        pd = ParallelDisplay(_ctx_from_console(console))
        pd.emit_phase_transition(
            "phase_a",
            "phase_b",
            pipeline_policy=policy,
        )
        output = console.export_text()
        assert "Phase A" in output, (
            f"Transition ({from_role}->{to_role}) should not contain duplicated status prose"
        )


def test_transition_without_policy_renders_as_minor_no_description() -> None:
    """Without policy, transitions are always minor with no description."""
    console = Console(record=True, width=120)
    pd = ParallelDisplay(_ctx_from_console(console))
    pd.emit_phase_transition(
        "development",
        "development_analysis",
    )
    output = console.export_text()
    assert "Development" in output
    assert "Development Analysis" in output


# --- Tests for show_phase_start_from_entry (canonical API) ---


def test_show_phase_start_from_entry_outer_dev_label() -> None:
    """show_phase_start_from_entry renders canonical Dev N/cap label."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=200)
    entry = PhaseEntryModel(phase_name="development", outer_dev_iteration=3, outer_dev_cap=7)
    pd = ParallelDisplay(_ctx_from_console(console))
    pd.emit_phase_start_from_entry(entry)
    output = buf.getvalue()
    assert "Development" in output
    assert "Dev 3/7" in output
    assert "Dev #3" not in output


def test_show_phase_start_from_entry_inner_analysis_label() -> None:
    """show_phase_start_from_entry renders canonical Analysis N/cap label."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=200)
    entry = PhaseEntryModel(
        phase_name="development_analysis", inner_analysis=2, inner_analysis_cap=3
    )
    pd = ParallelDisplay(_ctx_from_console(console))
    pd.emit_phase_start_from_entry(entry)
    output = buf.getvalue()
    assert "Development Analysis" in output
    assert "Analysis 2/3" in output


def test_show_phase_start_from_entry_no_raw_counter_format() -> None:
    """show_phase_start_from_entry never emits legacy [counter_name N/cap] format."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=200)
    entry = PhaseEntryModel(
        phase_name="development",
        outer_dev_iteration=2,
        outer_dev_cap=5,
        inner_analysis=1,
        inner_analysis_cap=3,
    )
    pd = ParallelDisplay(_ctx_from_console(console))
    pd.emit_phase_start_from_entry(entry)
    output = buf.getvalue()
    assert "[iteration" not in output
    assert "[reviewer_pass" not in output
    assert "Dev 2/5" in output
    assert "Analysis 1/3" in output


# --- Tests for default mode banners ---


def _make_execution_to_analysis_policy() -> PipelinePolicy:
    """Build a policy with execution \u2192 analysis \u2192 terminal for mode transition tests."""
    return PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                role="execution",
                transitions=PhaseTransition(on_success="development"),
            ),
            "development": PhaseDefinition(
                drain="development",
                role="analysis",
                transitions=PhaseTransition(on_success="done"),
            ),
            "done": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done"),
            ),
        },
        entry_phase="planning",
        terminal_phase="done",
        recovery=RecoveryPolicy(failed_route="done"),
    )


def test_show_phase_transition_at_medium_width_has_one_rule_no_description() -> None:
    """Default mode at medium width major transition uses a single titled Rule (no duplication)."""
    console = Console(record=True, width=80)
    ctx = make_display_context(
        console=console,
    )
    policy = _make_execution_to_analysis_policy()
    pd = ParallelDisplay(ctx)
    pd.emit_phase_transition("planning", "development", pipeline_policy=policy)
    output = console.export_text()

    assert "Planning" in output
    assert "Development" in output
    lines = output.split("\n")
    rule_lines = [line for line in lines if "\u2500" in line or "\u2501" in line]
    assert rule_lines, "expected at least one rule line for default mode at medium width"
    assert "Work complete" not in output
    assert "analyzing results" not in output


def test_show_phase_transition_at_wide_width_has_one_rule_no_description() -> None:
    """Default mode at wide width major transition uses a single titled Rule."""
    console = Console(record=True, width=120)
    ctx = make_display_context(
        console=console,
    )
    policy = _make_execution_to_analysis_policy()
    pd = ParallelDisplay(ctx)
    pd.emit_phase_transition("planning", "development", pipeline_policy=policy)
    output = console.export_text()

    assert "Planning" in output
    assert "Development" in output
    lines = output.split("\n")
    rule_lines = [line for line in lines if "\u2500" in line or "\u2501" in line]
    assert rule_lines, "expected at least one rule line for default mode at wide width"
    assert "Work complete" not in output
    assert "analyzing results" not in output


def _make_two_phase_policy(
    from_role: str,
    to_role: str,
    from_name: str = "phase_a",
    to_name: str = "phase_b",
) -> PipelinePolicy:
    """Build a minimal two-phase PipelinePolicy for display tests."""
    terminal_name = "done"
    return PipelinePolicy(
        phases={
            from_name: PhaseDefinition(
                drain=from_name,
                role=from_role,
                transitions=PhaseTransition(on_success=to_name),
            ),
            to_name: PhaseDefinition(
                drain=to_name,
                role=to_role,
                transitions=PhaseTransition(on_success=terminal_name),
            ),
            terminal_name: PhaseDefinition(
                drain=terminal_name,
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success=terminal_name),
            ),
        },
        entry_phase=from_name,
        terminal_phase=terminal_name,
        recovery=RecoveryPolicy(failed_route=terminal_name),
    )


class TestAnalysisExecutionTransitionBannerContext:
    """Verify analysis \u2192 execution banners render decision context."""

    def test_analysis_to_execution_banner_shows_decision_and_final_skip(self) -> None:
        """Analysis \u2192 execution transition shows decision in banner."""
        policy = _make_two_phase_policy(
            "analysis",
            "execution",
            "planning_analysis",
            "planning",
        )
        console = Console(record=True, width=120)
        context = {
            "analysis_status": "final, skipping next",
            "decision": "needs changes",
        }
        pd = ParallelDisplay(_ctx_from_console(console))
        pd.emit_phase_transition(
            "planning_analysis",
            "planning",
            context=context,
            pipeline_policy=policy,
        )
        output = console.export_text()
        assert "final, skipping next" in output, (
            f"Final-skip indicator missing from banner. Output:\n{output}"
        )
        assert "\u2192 needs changes" in output, f"Decision missing from banner. Output:\n{output}"

    def test_analysis_to_execution_banner_without_final_skip_shows_decision(self) -> None:
        """Analysis \u2192 execution transition shows decision even without final-skip."""
        policy = _make_two_phase_policy("analysis", "execution", "planning_analysis", "planning")
        console = Console(record=True, width=120)
        context = {
            "decision": "needs changes",
        }
        pd = ParallelDisplay(_ctx_from_console(console))
        pd.emit_phase_transition(
            "planning_analysis",
            "planning",
            context=context,
            pipeline_policy=policy,
        )
        output = console.export_text()
        assert "\u2192 needs changes" in output, f"Decision missing from banner. Output:\n{output}"
        assert "final, skipping next" not in output

    def test_analysis_to_commit_banner_shows_approved_decision(self) -> None:
        """Analysis \u2192 commit transition shows approved decision in banner."""
        policy = _make_two_phase_policy(
            "analysis", "commit", "planning_analysis", "planning_commit"
        )
        console = Console(record=True, width=120)
        context = {"decision": "approved"}
        pd = ParallelDisplay(_ctx_from_console(console))
        pd.emit_phase_transition(
            "planning_analysis",
            "planning_commit",
            context=context,
            pipeline_policy=policy,
        )
        output = console.export_text()
        assert "\u2192 approved" in output, (
            f"Approved decision missing from banner. Output:\n{output}"
        )
