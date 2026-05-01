"""Tests for ralph/display/phase_banner.py — phase transition display."""

from __future__ import annotations

import types
from io import StringIO

from rich.console import Console

from ralph.display.context import DisplayContext, make_display_context
from ralph.display.phase_banner import (
    _MAJOR_ROLE_PAIRS,
    _ROLE_PAIR_DESCRIPTIONS,
    PhaseStartContext,
    _phase_label,
    _phase_style,
    show_phase_complete,
    show_phase_start,
    show_phase_start_from_state,
    show_phase_transition,
)
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


def test_show_phase_start_with_iteration() -> None:
    console = Console(record=True)
    ctx = PhaseStartContext(budget_progress={"iteration": (1, 5)})
    show_phase_start("development", ctx=ctx, display_context=_ctx_from_console(console))
    output = console.export_text()
    assert "Development" in output
    assert "2/5" in output


def test_show_phase_start_with_reviewer_pass() -> None:
    console = Console(record=True)
    ctx = PhaseStartContext(budget_progress={"reviewer_pass": (0, 3)})
    show_phase_start("review", ctx=ctx, display_context=_ctx_from_console(console))
    output = console.export_text()
    assert "Review" in output
    assert "1/3" in output


def test_show_phase_complete_with_decision() -> None:
    console = Console(record=True)
    show_phase_complete(
        "review_analysis", decision="approved", display_context=_ctx_from_console(console)
    )
    output = console.export_text()
    assert "approved" in output
    assert "Review Analysis" in output


def test_phase_label_converts_underscore_names() -> None:
    assert _phase_label("development_analysis") == "Development Analysis"
    assert _phase_label("review_commit") == "Review Commit"
    assert _phase_label("planning") == "Planning"


def test_phase_style_canonical_names_without_policy_return_muted() -> None:
    assert _phase_style("planning") == "theme.text.muted"
    assert _phase_style("development") == "theme.text.muted"
    assert _phase_style("complete") == "theme.text.muted"
    assert _phase_style("failed") == "theme.text.muted"


def test_phase_style_role_names_without_policy_return_correct_styles() -> None:
    assert _phase_style("review") == "theme.phase.review"
    assert _phase_style("fix") == "theme.phase.fix"
    assert _phase_style("execution") == "theme.phase.development"
    assert _phase_style("terminal") == "theme.phase.complete"


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


def test_show_phase_start_zero_indexed_boundary() -> None:
    """Iteration 0 should display as 1 (1-indexed for users)."""
    console = Console(record=True)
    ctx = PhaseStartContext(budget_progress={"iteration": (0, 5)})
    show_phase_start("development", ctx=ctx, display_context=_ctx_from_console(console))
    output = console.export_text()
    assert "1/5" in output


def test_show_phase_start_last_iteration_boundary() -> None:
    """Last iteration (N-1) should display as N/N."""
    console = Console(record=True)
    ctx = PhaseStartContext(budget_progress={"iteration": (4, 5)})
    show_phase_start("development", ctx=ctx, display_context=_ctx_from_console(console))
    output = console.export_text()
    assert "5/5" in output


def test_show_phase_complete_without_decision() -> None:
    console = Console(record=True)
    show_phase_complete("development", display_context=_ctx_from_console(console))
    output = console.export_text()
    assert "Development" in output
    assert "complete" in output


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
    """Analysis approved → commit should be a major transition when policy is provided."""
    policy = _make_two_phase_policy("analysis", "commit", "dev_analysis", "dev_commit")
    console = Console(record=True, width=120)
    show_phase_transition(
        "dev_analysis", "dev_commit",
        pipeline_policy=policy,
        display_context=_ctx_from_console(console),
    )
    output = console.export_text()
    assert "Dev Analysis" in output
    assert "Dev Commit" in output
    assert "Analysis approved" in output


def test_major_transition_analysis_loopback_with_policy() -> None:
    """Analysis loopback → execution should be a major transition when policy is provided."""
    policy = _make_two_phase_policy("analysis", "execution", "check", "work")
    console = Console(record=True, width=120)
    show_phase_transition(
        "check", "work",
        pipeline_policy=policy,
        display_context=_ctx_from_console(console),
    )
    output = console.export_text()
    assert "Check" in output
    assert "Work" in output
    assert "Analysis requested changes" in output


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
        "review_phase", terminal_name,
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
        "unknown_phase", "another_unknown",
        display_context=_ctx_from_console(console),
    )
    output = console.export_text()
    assert "Unknown Phase" in output
    assert "Another Unknown" in output


def test_all_major_role_pairs_have_descriptions() -> None:
    """Every major role-pair transition should have a description for good UX."""
    for from_role, to_role in _MAJOR_ROLE_PAIRS:
        assert (from_role, to_role) in _ROLE_PAIR_DESCRIPTIONS, (
            f"Major role-pair ({from_role}, {to_role}) has no description"
        )


def test_role_pair_descriptions_render_in_major_banners() -> None:
    """Major role-pair transitions should include the description text in output."""
    for (from_role, to_role), description in _ROLE_PAIR_DESCRIPTIONS.items():
        if (from_role, to_role) not in _MAJOR_ROLE_PAIRS:
            continue
        policy = _make_two_phase_policy(from_role, to_role, "phase_a", "phase_b")
        console = Console(record=True, width=120)
        show_phase_transition(
            "phase_a", "phase_b",
            pipeline_policy=policy,
            display_context=_ctx_from_console(console),
        )
        output = console.export_text()
        assert description[:20] in output, (
            f"Description '{description}' not found for role-pair ({from_role}, {to_role})"
        )


def test_transition_without_policy_renders_as_minor_no_description() -> None:
    """Without policy, transitions are always minor with no description."""
    console = Console(record=True, width=120)
    show_phase_transition(
        "development", "development_analysis",
        display_context=_ctx_from_console(console),
    )
    output = console.export_text()
    assert "Development" in output
    assert "Development Analysis" in output


def test_show_phase_start_reviewer_pass_zero_boundary() -> None:
    """Reviewer pass 0 should display as 1/N (1-indexed)."""
    console = Console(record=True)
    ctx = PhaseStartContext(budget_progress={"reviewer_pass": (0, 3)})
    show_phase_start("review", ctx=ctx, display_context=_ctx_from_console(console))
    output = console.export_text()
    assert "1/3" in output


def test_show_phase_start_reviewer_pass_last_boundary() -> None:
    """Last reviewer pass (N-1) should display as N/N."""
    console = Console(record=True)
    ctx = PhaseStartContext(budget_progress={"reviewer_pass": (2, 3)})
    show_phase_start("review", ctx=ctx, display_context=_ctx_from_console(console))
    output = console.export_text()
    assert "3/3" in output


# --- New tests for analysis iteration counters (Step 5) ---


def test_show_phase_start_dev_analysis_shows_analysis_counter() -> None:
    """When phase is development_analysis with counter context, suffix [analysis N/M] appears."""
    console = Console(record=True)
    ctx = PhaseStartContext(
        analysis_iteration=1,
        max_analysis_iterations=3,
    )
    show_phase_start("development_analysis", ctx=ctx, display_context=_ctx_from_console(console))
    output = console.export_text()
    assert "Development Analysis" in output
    assert "[analysis 2/3]" in output


def test_show_phase_start_dev_analysis_zero_index_shows_one() -> None:
    """analysis_iteration=0 shows as [analysis 1/M]."""
    console = Console(record=True)
    ctx = PhaseStartContext(
        analysis_iteration=0,
        max_analysis_iterations=3,
    )
    show_phase_start("development_analysis", ctx=ctx, display_context=_ctx_from_console(console))
    output = console.export_text()
    assert "[analysis 1/3]" in output


def test_show_phase_start_dev_analysis_at_max_shows_max() -> None:
    """analysis_iteration=2 with max=3 shows [analysis 3/3]."""
    console = Console(record=True)
    ctx = PhaseStartContext(
        analysis_iteration=2,
        max_analysis_iterations=3,
    )
    show_phase_start("development_analysis", ctx=ctx, display_context=_ctx_from_console(console))
    output = console.export_text()
    assert "[analysis 3/3]" in output


def test_show_phase_start_review_analysis_shows_analysis_counter() -> None:
    """When phase is review_analysis with counter context, suffix [analysis N/M] appears."""
    console = Console(record=True)
    ctx = PhaseStartContext(
        analysis_iteration=0,
        max_analysis_iterations=2,
    )
    show_phase_start("review_analysis", ctx=ctx, display_context=_ctx_from_console(console))
    output = console.export_text()
    assert "Review Analysis" in output
    assert "[analysis 1/2]" in output


def test_show_phase_start_review_analysis_at_max_shows_max() -> None:
    """analysis_iteration=1 with max=2 shows [analysis 2/2]."""
    console = Console(record=True)
    ctx = PhaseStartContext(
        analysis_iteration=1,
        max_analysis_iterations=2,
    )
    show_phase_start("review_analysis", ctx=ctx, display_context=_ctx_from_console(console))
    output = console.export_text()
    assert "[analysis 2/2]" in output


def test_show_phase_start_dev_analysis_no_suffix_without_context() -> None:
    """When phase is development_analysis but no counter context, no [analysis] suffix."""
    console = Console(record=True)
    ctx = PhaseStartContext(
        budget_progress={"iteration": (0, 5)},
        # No analysis_iteration or max_analysis_iterations set
    )
    show_phase_start("development_analysis", ctx=ctx, display_context=_ctx_from_console(console))
    output = console.export_text()
    assert "Development Analysis" in output
    assert "[analysis" not in output


def test_show_phase_start_development_no_analysis_suffix() -> None:
    """When phase is development without analysis_iteration, no [analysis] suffix."""
    console = Console(record=True)
    ctx = PhaseStartContext(
        budget_progress={"iteration": (1, 5)},
    )
    show_phase_start("development", ctx=ctx, display_context=_ctx_from_console(console))
    output = console.export_text()
    assert "Development" in output
    assert "[analysis" not in output


def test_show_phase_start_review_no_analysis_suffix() -> None:
    """When phase is review without analysis_iteration, no [analysis] suffix."""
    console = Console(record=True)
    ctx = PhaseStartContext(
        budget_progress={"reviewer_pass": (0, 2)},
    )
    show_phase_start("review", ctx=ctx, display_context=_ctx_from_console(console))
    output = console.export_text()
    assert "Review" in output
    assert "[analysis" not in output


def test_show_phase_start_combines_iteration_and_analysis_counters() -> None:
    """Both iteration and analysis counters can appear together."""
    console = Console(record=True)
    ctx = PhaseStartContext(
        budget_progress={"iteration": (2, 5)},
        analysis_iteration=1,
        max_analysis_iterations=3,
    )
    show_phase_start("development_analysis", ctx=ctx, display_context=_ctx_from_console(console))
    output = console.export_text()
    assert "Development Analysis" in output
    assert "[iteration 3/5]" in output
    assert "[analysis 2/3]" in output


# --- Tests for show_phase_start_from_state (Step 13) ---


def test_show_phase_start_from_state_forwards_counters() -> None:
    stub = types.SimpleNamespace(
        outer_progress={"iteration": 0, "reviewer_pass": 1},
        budget_caps={"iteration": 3, "reviewer_pass": 2},
        agent_name="coder",
    )
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=200)
    show_phase_start_from_state(stub, "development", display_context=_ctx_from_console(console))
    output = buf.getvalue()
    assert "iteration 1/3" in output
    assert "pass 2/2" in output
    assert "Development" in output


def test_show_phase_start_from_state_tolerates_missing_attrs() -> None:
    stub = types.SimpleNamespace(outer_progress={"iteration": 0}, budget_caps={"iteration": 3})
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=200)
    show_phase_start_from_state(stub, "planning", display_context=_ctx_from_console(console))
    output = buf.getvalue()
    assert "iteration 1/3" in output
    assert "Planning" in output
    assert "pass" not in output


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


def test_show_phase_transition_medium_mode_has_two_rules_with_description() -> None:
    """Medium mode major transition keeps both Rules and the description text."""
    console = Console(record=True, width=80)
    ctx = make_display_context(console=console, force_mode="medium")
    policy = _make_execution_to_analysis_policy()
    show_phase_transition("planning", "development", pipeline_policy=policy, display_context=ctx)
    output = console.export_text()

    assert "Planning" in output
    assert "Development" in output
    # Medium should have two Rule lines (opening and closing)
    lines = output.split("\n")
    rule_lines = [line for line in lines if "─" in line or "━" in line]
    expected_rule_count = 2
    assert len(rule_lines) == expected_rule_count, (
        f"Expected {expected_rule_count} rule lines for medium mode, got: {rule_lines}"
    )
    # Medium should still preserve the description text (execution → analysis)
    assert "Work complete" in output


def test_show_phase_transition_wide_mode_has_description_and_leading_blank() -> None:
    """Wide mode major transition has leading blank, description text, and two Rules."""
    console = Console(record=True, width=120)
    ctx = make_display_context(console=console, force_mode="wide")
    policy = _make_execution_to_analysis_policy()
    show_phase_transition("planning", "development", pipeline_policy=policy, display_context=ctx)
    output = console.export_text()

    assert "Planning" in output
    assert "Development" in output
    # Wide should have leading blank line
    lines = output.split("\n")
    assert lines[0] == ""  # First line is blank
    # Wide should have description text (execution → analysis)
    assert "Work complete" in output
    # Wide should have two Rule lines
    rule_lines = [line for line in lines if "─" in line or "━" in line]
    expected_rule_count = 2
    assert len(rule_lines) == expected_rule_count


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
        style = _phase_style("my_work", pipeline_policy=policy)
        assert style == "theme.phase.development"

    def test_phase_style_analysis_role(self) -> None:
        """A phase with analysis role resolves to development_analysis theme."""
        policy = _make_two_phase_policy("execution", "analysis", "work", "inspect")
        style = _phase_style("inspect", pipeline_policy=policy)
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
        style = _phase_style(terminal_name, pipeline_policy=policy)
        assert style == "theme.phase.failed"

    def test_transition_uses_role_pair_for_major_detection(self) -> None:
        """execution→analysis transition treated as major when policy provides roles."""
        policy = _make_two_phase_policy("execution", "analysis", "my_work", "my_check")
        console = Console(record=True)
        show_phase_transition("my_work", "my_check", pipeline_policy=policy, console=console)
        output = console.export_text()
        # Major transition produces a Rule with the phase label
        assert "My Work" in output
        assert "My Check" in output

    def test_transition_without_policy_renders_as_minor(self) -> None:
        """No policy → transition renders as minor (no description)."""
        console = Console(record=True)
        show_phase_transition("planning", "development", console=console)
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

    def test_show_phase_complete_with_policy_uses_role_style(self) -> None:
        """show_phase_complete passes through pipeline_policy to _phase_style."""
        policy = _make_two_phase_policy("execution", "analysis", "my_work", "my_check")
        console = Console(record=True)
        show_phase_complete("my_work", pipeline_policy=policy, console=console)
        output = console.export_text()
        assert "My Work" in output
