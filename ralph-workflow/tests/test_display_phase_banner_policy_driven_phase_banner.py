"""Tests for ralph/display/phase_banner.py — phase transition display."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.context import DisplayContext, make_display_context
from ralph.display.phase_banner import (
    MAJOR_ROLE_PAIRS,
    phase_label,
    phase_style,
    show_phase_start,
    show_phase_start_from_entry,
    show_phase_transition,
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
    show_phase_transition("planning", "development", display_context=_ctx_from_console(console))
    output = console.export_text()
    assert "Planning" in output
    assert "Development" in output


def test_show_phase_transition_minor_renders_rule() -> None:
    console = Console(record=True)
    show_phase_transition(
        "development", "development_analysis", display_context=_ctx_from_console(console)
    )
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
    show_phase_start("planning", display_context=_ctx_from_console(console))
    output = console.export_text()
    assert "Planning" in output
    assert "▶" in output


def test_show_phase_start_with_agent_name() -> None:
    console = Console(record=True)
    show_phase_start("development", agent_name="claude", display_context=_ctx_from_console(console))
    output = console.export_text()
    assert "Development" in output
    assert "claude" in output


def test_show_phase_transition_with_context() -> None:
    # Context dict is only rendered for major transitions (requires a policy that resolves
    # to a major role pair). Use execution → analysis as the canonical major pair.
    policy = _make_two_phase_policy("execution", "analysis", "planning", "analysis_phase")
    console = Console(record=True, width=120)
    show_phase_transition(
        "planning",
        "analysis_phase",
        context={"iteration": "1/5"},
        pipeline_policy=policy,
        display_context=_ctx_from_console(console),
    )
    output = console.export_text()
    assert "Planning" in output
    assert "Analysis Phase" in output
    assert "iteration=1/5" in output


# --- New tests for expanded transitions and descriptions ---


def test_major_transition_analysis_to_commit_with_policy() -> None:
    """Analysis → commit is a major transition that shows routing labels without status prose."""
    policy = _make_two_phase_policy("analysis", "commit", "dev_analysis", "dev_commit")
    console = Console(record=True, width=120)
    show_phase_transition(
        "dev_analysis",
        "dev_commit",
        pipeline_policy=policy,
        display_context=_ctx_from_console(console),
    )
    output = console.export_text()
    assert "Dev Analysis" in output
    assert "Dev Commit" in output
    # Phase-close banner handles status prose; transition shows only routing
    assert "Analysis approved" not in output


def test_major_transition_analysis_loopback_with_policy() -> None:
    """Analysis loopback → execution is a major transition with routing labels only."""
    policy = _make_two_phase_policy("analysis", "execution", "check", "work")
    console = Console(record=True, width=120)
    show_phase_transition(
        "check",
        "work",
        pipeline_policy=policy,
        display_context=_ctx_from_console(console),
    )
    output = console.export_text()
    assert "Check" in output
    assert "Work" in output
    # Phase-close banner handles status prose; transition shows only routing
    assert "Analysis requested changes" not in output


def test_major_transition_review_to_terminal_with_policy() -> None:
    """Review → terminal should be a major transition when policy provides roles."""
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
    show_phase_transition(
        "review_phase",
        terminal_name,
        pipeline_policy=policy,
        display_context=_ctx_from_console(console),
    )
    output = console.export_text()
    assert "Review Phase" in output
    assert "Done" in output


def test_unknown_transition_renders_gracefully() -> None:
    """Unknown transition pair should still render without crashing."""
    console = Console(record=True, width=120)
    show_phase_transition(
        "unknown_phase",
        "another_unknown",
        display_context=_ctx_from_console(console),
    )
    output = console.export_text()
    assert "Unknown Phase" in output
    assert "Another Unknown" in output


def test_major_role_pairs_transition_shows_no_duplicated_description() -> None:
    """Major role-pair transitions must not show duplicated description prose.

    The phase-close banner already communicates exit context via exit_trigger;
    the transition banner should not repeat status prose.
    """
    for from_role, to_role in MAJOR_ROLE_PAIRS:
        policy = _make_two_phase_policy(from_role, to_role, "phase_a", "phase_b")
        console = Console(record=True, width=120)
        show_phase_transition(
            "phase_a",
            "phase_b",
            pipeline_policy=policy,
            display_context=_ctx_from_console(console),
        )
        output = console.export_text()
        # Transition must not contain status prose that the phase-close banner handles
        assert "complete" not in output.lower().split("—")[0] or "Phase A" in output, (
            f"Transition ({from_role}->{to_role}) should not contain duplicated status prose"
        )


def test_transition_without_policy_renders_as_minor_no_description() -> None:
    """Without policy, transitions are always minor with no description."""
    console = Console(record=True, width=120)
    show_phase_transition(
        "development",
        "development_analysis",
        display_context=_ctx_from_console(console),
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
    show_phase_start_from_entry(entry, display_context=_ctx_from_console(console))
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
    show_phase_start_from_entry(entry, display_context=_ctx_from_console(console))
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
    show_phase_start_from_entry(entry, display_context=_ctx_from_console(console))
    output = buf.getvalue()
    assert "[iteration" not in output
    assert "[reviewer_pass" not in output
    assert "Dev 2/5" in output
    assert "Analysis 1/3" in output


# --- Tests for compact/medium/wide mode banners ---


def _make_execution_to_analysis_policy() -> PipelinePolicy:
    """Build a policy with execution → analysis → terminal for mode transition tests."""
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


def test_show_phase_transition_compact_mode_no_leading_blank_line() -> None:
    """Compact mode major transition has no leading blank line and one Rule."""
    console = Console(record=True, width=80)
    ctx = make_display_context(console=console, force_mode="compact")
    policy = _make_execution_to_analysis_policy()
    show_phase_transition("planning", "development", pipeline_policy=policy, display_context=ctx)
    output = console.export_text()

    assert "Planning" in output
    assert "Development" in output
    # Compact: no leading blank line (first char should be the Rule character)
    lines = output.split("\n")
    assert lines[0].strip() != ""  # First line is not blank
    # Should have exactly one Rule line (compact shows single rule with title)
    rule_lines = [line for line in lines if "─" in line or "━" in line]
    assert len(rule_lines) == 1


def test_show_phase_transition_medium_mode_has_one_rule_no_description() -> None:
    """Medium mode major transition uses a single titled Rule (no duplication).

    The phase-close banner already communicates exit context; the transition
    banner shows only routing context (from-phase → to-phase).
    """
    console = Console(record=True, width=80)
    ctx = make_display_context(console=console, force_mode="medium")
    policy = _make_execution_to_analysis_policy()
    show_phase_transition("planning", "development", pipeline_policy=policy, display_context=ctx)
    output = console.export_text()

    assert "Planning" in output
    assert "Development" in output
    lines = output.split("\n")
    rule_lines = [line for line in lines if "─" in line or "━" in line]
    assert len(rule_lines) == 1, f"Expected 1 rule line for medium mode, got: {rule_lines}"
    # Must NOT contain duplicated transition description prose
    assert "Work complete" not in output
    assert "analyzing results" not in output


def test_show_phase_transition_wide_mode_has_one_rule_no_description() -> None:
    """Wide mode major transition uses a single titled Rule (same as compact/medium).

    The phase-close banner already communicates exit context; the transition
    banner shows only routing context (from-phase → to-phase).
    """
    console = Console(record=True, width=120)
    ctx = make_display_context(console=console, force_mode="wide")
    policy = _make_execution_to_analysis_policy()
    show_phase_transition("planning", "development", pipeline_policy=policy, display_context=ctx)
    output = console.export_text()

    assert "Planning" in output
    assert "Development" in output
    lines = output.split("\n")
    rule_lines = [line for line in lines if "─" in line or "━" in line]
    assert len(rule_lines) == 1, f"Expected 1 rule line for wide mode, got: {rule_lines}"
    # Must NOT contain duplicated transition description prose
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


class TestPolicyDrivenPhaseBanner:
    """Phase banner renders correctly when a PipelinePolicy provides role context."""

    def test_phase_style_uses_role_over_name(self) -> None:
        """A renamed execution phase gets the execution style, not muted fallback."""
        policy = _make_two_phase_policy("execution", "analysis", "my_work", "my_check")
        style = phase_style("my_work", pipeline_policy=policy)
        assert style == "theme.phase.development"

    def test_phase_style_analysis_role(self) -> None:
        """A phase with analysis role resolves to development_analysis theme."""
        policy = _make_two_phase_policy("execution", "analysis", "work", "inspect")
        style = phase_style("inspect", pipeline_policy=policy)
        assert style == "theme.phase.development_analysis"

    def test_phase_style_terminal_failure_role(self) -> None:
        """A terminal-failure phase gets the failed theme style."""
        terminal_name = "fail"
        policy = PipelinePolicy(
            phases={
                "start": PhaseDefinition(
                    drain="start",
                    role="execution",
                    transitions=PhaseTransition(on_success=terminal_name),
                ),
                terminal_name: PhaseDefinition(
                    drain=terminal_name,
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success=terminal_name),
                ),
            },
            entry_phase="start",
            terminal_phase=terminal_name,
            recovery=RecoveryPolicy(failed_route=terminal_name),
        )
        style = phase_style(terminal_name, pipeline_policy=policy)
        assert style == "theme.phase.failed"

    def test_transition_uses_role_pair_for_major_detection(self) -> None:
        """execution→analysis transition treated as major when policy provides roles."""
        policy = _make_two_phase_policy("execution", "analysis", "my_work", "my_check")
        console = Console(record=True)
        show_phase_transition(
            "my_work", "my_check",
            pipeline_policy=policy,
            display_context=make_display_context(console=console),
        )
        output = console.export_text()
        # Major transition produces a Rule with the phase label
        assert "My Work" in output
        assert "My Check" in output

    def test_transition_without_policy_renders_as_minor(self) -> None:
        """No policy → transition renders as minor (no description)."""
        console = Console(record=True)
        show_phase_transition(
            "planning", "development",
            display_context=make_display_context(console=console),
        )
        output = console.export_text()
        assert "Planning" in output
        assert "Development" in output

    def test_show_phase_start_with_policy_uses_role_style(self) -> None:
        """show_phase_start passes through pipeline_policy to _phase_style."""
        policy = _make_two_phase_policy("execution", "analysis", "my_work", "my_check")
        console = Console(record=True)
        show_phase_start("my_work", pipeline_policy=policy, console=console)
        output = console.export_text()
        assert "My Work" in output
