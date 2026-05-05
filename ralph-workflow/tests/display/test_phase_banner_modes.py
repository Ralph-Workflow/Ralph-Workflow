"""Mode and glyph tests for ralph/display/phase_banner.py.

Tests the adaptive layout across compact/medium/wide modes and
the ASCII glyph fallback when RALPH_FORCE_ASCII=1.
"""

from __future__ import annotations

from io import StringIO
from typing import Literal

from rich.console import Console

from ralph.display.context import DisplayContext, make_display_context
from ralph.display.phase_banner import (
    show_phase_close_banner,
    show_phase_start,
    show_phase_start_from_entry,
    show_phase_transition,
)
from ralph.display.phase_lifecycle import PhaseEntryModel, PhaseExitModel
from ralph.display.theme import ASCII_GLYPHS, UNICODE_GLYPHS
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy, RecoveryPolicy

_WIDE_EXPECTED_RULES = 2
_WIDE_PHASE_START_RULES = 1


def _make_ctx(
    mode: Literal["compact", "medium", "wide"],
    *,
    ascii_mode: bool = False,
) -> DisplayContext:
    """Create a DisplayContext for the given mode and glyph setting."""
    buf = StringIO()
    width = {"compact": 50, "medium": 80, "wide": 120}[mode]
    console = Console(file=buf, record=True, force_terminal=False, color_system=None, width=width)
    env: dict[str, str] = {}
    if ascii_mode:
        env["RALPH_FORCE_ASCII"] = "1"
    return make_display_context(console=console, env=env, force_mode=mode)


def _export(ctx: DisplayContext) -> str:
    return ctx.console.export_text()


def _make_execution_to_analysis_policy() -> PipelinePolicy:
    """A policy with 'design' (execution) → 'audit' (analysis) producing a major transition."""
    return PipelinePolicy(
        entry_phase="design",
        terminal_phase="done",
        phases={
            "design": PhaseDefinition(
                drain="design",
                role="execution",
                transitions=PhaseTransition(on_success="audit"),
            ),
            "audit": PhaseDefinition(
                drain="audit",
                role="analysis",
                transitions=PhaseTransition(on_success="done"),
            ),
            "halt": PhaseDefinition(
                drain="halt",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="halt", on_loopback="halt"),
            ),
            "done": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done", on_loopback="done"),
            ),
        },
        recovery=RecoveryPolicy(failed_route="halt"),
    )


# --- Compact mode ---

def test_compact_major_transition_no_leading_blank() -> None:
    """Compact: major transition emits no leading blank line."""
    ctx = _make_ctx("compact")
    policy = _make_execution_to_analysis_policy()
    show_phase_transition("design", "audit", pipeline_policy=policy, display_context=ctx)
    output = _export(ctx)
    lines = output.split("\n")
    assert lines[0].strip() != "", "Compact mode must not have a leading blank line"


def test_compact_major_transition_one_rule() -> None:
    """Compact: major transition emits exactly one Rule (trailing)."""
    ctx = _make_ctx("compact")
    policy = _make_execution_to_analysis_policy()
    show_phase_transition("design", "audit", pipeline_policy=policy, display_context=ctx)
    output = _export(ctx)
    rule_lines = [ln for ln in output.split("\n") if "─" in ln or "━" in ln]
    assert len(rule_lines) == 1, f"Expected 1 rule line, got {len(rule_lines)}: {rule_lines}"


# --- Wide mode ---

def test_wide_major_transition_has_two_rules() -> None:
    """Wide: major transition emits two Rules (separator + trailing)."""
    ctx = _make_ctx("wide")
    policy = _make_execution_to_analysis_policy()
    show_phase_transition("design", "audit", pipeline_policy=policy, display_context=ctx)
    output = _export(ctx)
    rule_lines = [ln for ln in output.split("\n") if "─" in ln or "━" in ln]
    assert len(rule_lines) == _WIDE_EXPECTED_RULES, (
        f"Expected {_WIDE_EXPECTED_RULES} rule lines, got {len(rule_lines)}: {rule_lines}"
    )


def test_wide_major_transition_has_leading_blank() -> None:
    """Wide: major transition starts with a blank line."""
    ctx = _make_ctx("wide")
    policy = _make_execution_to_analysis_policy()
    show_phase_transition("design", "audit", pipeline_policy=policy, display_context=ctx)
    output = _export(ctx)
    lines = output.split("\n")
    assert lines[0] == "", "Wide mode must start with a blank line"


# --- ASCII glyph fallbacks ---

def test_arrow_glyph_is_unicode_by_default() -> None:
    """Without ASCII override, arrow glyph is the Unicode arrow."""
    ctx = _make_ctx("wide", ascii_mode=False)
    show_phase_transition("planning", "development", display_context=ctx)
    output = _export(ctx)
    assert UNICODE_GLYPHS["arrow"] in output


def test_arrow_glyph_swaps_to_ascii_with_force_ascii() -> None:
    """With RALPH_FORCE_ASCII=1, arrow glyph becomes ASCII fallback '->'."""
    ctx = _make_ctx("wide", ascii_mode=True)
    show_phase_transition("planning", "development", display_context=ctx)
    output = _export(ctx)
    assert ASCII_GLYPHS["arrow"] in output
    assert UNICODE_GLYPHS["arrow"] not in output


def test_start_glyph_swaps_to_ascii_with_force_ascii() -> None:
    """With RALPH_FORCE_ASCII=1, start glyph becomes '>' in show_phase_start."""
    ctx = _make_ctx("wide", ascii_mode=True)
    show_phase_start("planning", display_context=ctx)
    output = _export(ctx)
    assert ASCII_GLYPHS["start"] in output
    assert UNICODE_GLYPHS["start"] not in output


# --- Phase-start ordering tests ---

def test_phase_start_outer_dev_appears_before_inner_analysis() -> None:
    """Dev label must appear before Analysis label in phase-start output."""
    ctx = _make_ctx("wide")
    entry = PhaseEntryModel(
        phase_name="analysis",
        outer_dev_iteration=3,
        inner_analysis=2,
        inner_analysis_cap=5,
    )
    show_phase_start_from_entry(entry, display_context=ctx)
    output = _export(ctx)
    dev_pos = output.find("Dev #3")
    analysis_pos = output.find("Analysis 2/5")
    assert dev_pos != -1, f"Expected 'Dev #3' in output, got: {output!r}"
    assert analysis_pos != -1, f"Expected 'Analysis 2/5' in output, got: {output!r}"
    assert dev_pos < analysis_pos, (
        f"Dev #3 must appear before Analysis 2/5, "
        f"but dev_pos={dev_pos} analysis_pos={analysis_pos}"
    )


def test_phase_start_no_outer_dev_only_inner_analysis() -> None:
    """Without outer_dev, inner_analysis still renders in output."""
    ctx = _make_ctx("wide")
    entry = PhaseEntryModel(
        phase_name="analysis",
        inner_analysis=1,
        inner_analysis_cap=3,
    )
    show_phase_start_from_entry(entry, display_context=ctx)
    output = _export(ctx)
    assert "Analysis 1/3" in output
    assert "Dev #" not in output


# --- Phase-close banner tests ---


def test_phase_close_banner_uses_unicode_checkmark_by_default() -> None:
    """show_phase_close_banner uses Unicode success glyph by default."""
    ctx = _make_ctx("wide", ascii_mode=False)
    exit_model = PhaseExitModel(phase_name="development")
    show_phase_close_banner(exit_model, display_context=ctx)
    output = _export(ctx)
    assert UNICODE_GLYPHS["success"] in output


def test_phase_close_banner_uses_ok_badge_in_ascii_mode() -> None:
    """With RALPH_FORCE_ASCII=1, show_phase_close_banner uses '[OK]'."""
    ctx = _make_ctx("wide", ascii_mode=True)
    exit_model = PhaseExitModel(phase_name="development")
    show_phase_close_banner(exit_model, display_context=ctx)
    output = _export(ctx)
    assert ASCII_GLYPHS["success"] in output
    assert UNICODE_GLYPHS["success"] not in output


def test_phase_close_banner_dev_appears_before_analysis() -> None:
    """Dev label must appear before Analysis label in phase-close banner."""
    ctx = _make_ctx("wide")
    exit_model = PhaseExitModel(
        phase_name="development_analysis",
        outer_dev_iteration=2,
        outer_dev_cap=3,
        inner_analysis=1,
        inner_analysis_cap=5,
    )
    show_phase_close_banner(exit_model, display_context=ctx)
    output = _export(ctx)
    dev_pos = output.find("Dev 2/3")
    analysis_pos = output.find("Analysis 1/5")
    assert dev_pos != -1, f"Expected 'Dev 2/3' in output, got: {output!r}"
    assert analysis_pos != -1, f"Expected 'Analysis 1/5' in output, got: {output!r}"
    assert dev_pos < analysis_pos, (
        f"Dev must appear before Analysis, but dev_pos={dev_pos} analysis_pos={analysis_pos}"
    )


def test_phase_close_banner_elapsed_appears_after_budget() -> None:
    """Elapsed time appears after budget remaining in phase-close banner."""
    ctx = _make_ctx("wide")
    exit_model = PhaseExitModel(
        phase_name="development",
        budget_remaining=1,
        elapsed_seconds=5.0,
    )
    show_phase_close_banner(exit_model, display_context=ctx)
    output = _export(ctx)
    budget_pos = output.find("Budget: 1 left")
    elapsed_pos = output.find("5.0s")
    assert budget_pos != -1, f"Expected 'Budget: 1 left' in output, got: {output!r}"
    assert elapsed_pos != -1, f"Expected '5.0s' in output, got: {output!r}"
    assert budget_pos < elapsed_pos, (
        f"Budget must appear before elapsed, but budget_pos={budget_pos} elapsed_pos={elapsed_pos}"
    )


def test_phase_close_banner_ascii_arrow_for_exit_trigger() -> None:
    """With RALPH_FORCE_ASCII=1, exit trigger arrow uses '->'."""
    ctx = _make_ctx("wide", ascii_mode=True)
    exit_model = PhaseExitModel(phase_name="development", exit_trigger="produced")
    show_phase_close_banner(exit_model, display_context=ctx)
    output = _export(ctx)
    assert ASCII_GLYPHS["arrow"] in output
    assert "produced" in output


# --- Outer/inner qualifier tests ---


def test_phase_start_wide_mode_shows_outer_qualifier() -> None:
    """In wide mode, show_phase_start_from_entry appends '(outer)' to dev cycle label."""
    ctx = _make_ctx("wide")
    entry = PhaseEntryModel(
        phase_name="development",
        outer_dev_iteration=2,
        outer_dev_cap=3,
    )
    show_phase_start_from_entry(entry, display_context=ctx)
    output = _export(ctx)
    assert "Dev 2/3" in output
    assert "(outer)" in output


def test_phase_start_compact_mode_omits_outer_qualifier() -> None:
    """In compact mode, show_phase_start_from_entry omits '(outer)' qualifier."""
    ctx = _make_ctx("compact")
    entry = PhaseEntryModel(
        phase_name="development",
        outer_dev_iteration=2,
        outer_dev_cap=3,
    )
    show_phase_start_from_entry(entry, display_context=ctx)
    output = _export(ctx)
    assert "Dev 2/3" in output
    assert "(outer)" not in output


def test_phase_start_medium_mode_shows_outer_qualifier() -> None:
    """In medium mode, show_phase_start_from_entry shows '(outer)' qualifier."""
    ctx = _make_ctx("medium")
    entry = PhaseEntryModel(
        phase_name="development",
        outer_dev_iteration=2,
        outer_dev_cap=3,
    )
    show_phase_start_from_entry(entry, display_context=ctx)
    output = _export(ctx)
    assert "Dev 2/3" in output
    assert "(outer)" in output


def test_phase_start_wide_mode_shows_inner_qualifier() -> None:
    """In wide mode, show_phase_start_from_entry appends '(inner)' to analysis cycle label."""
    ctx = _make_ctx("wide")
    entry = PhaseEntryModel(
        phase_name="development_analysis",
        inner_analysis=1,
        inner_analysis_cap=3,
    )
    show_phase_start_from_entry(entry, display_context=ctx)
    output = _export(ctx)
    assert "Analysis 1/3" in output
    assert "(inner)" in output


def test_phase_start_compact_mode_omits_inner_qualifier() -> None:
    """In compact mode, show_phase_start_from_entry omits '(inner)' qualifier."""
    ctx = _make_ctx("compact")
    entry = PhaseEntryModel(
        phase_name="development_analysis",
        inner_analysis=1,
        inner_analysis_cap=3,
    )
    show_phase_start_from_entry(entry, display_context=ctx)
    output = _export(ctx)
    assert "Analysis 1/3" in output
    assert "(inner)" not in output


def test_phase_close_wide_mode_shows_outer_qualifier() -> None:
    """In wide mode, show_phase_close_banner appends '(outer)' to dev cycle label."""
    ctx = _make_ctx("wide")
    exit_model = PhaseExitModel(
        phase_name="development",
        outer_dev_iteration=3,
        outer_dev_cap=5,
    )
    show_phase_close_banner(exit_model, display_context=ctx)
    output = _export(ctx)
    assert "Dev 3/5" in output
    assert "(outer)" in output


def test_phase_close_compact_mode_omits_outer_qualifier() -> None:
    """In compact mode, show_phase_close_banner omits '(outer)' qualifier."""
    ctx = _make_ctx("compact")
    exit_model = PhaseExitModel(
        phase_name="development",
        outer_dev_iteration=3,
        outer_dev_cap=5,
    )
    show_phase_close_banner(exit_model, display_context=ctx)
    output = _export(ctx)
    assert "Dev 3/5" in output
    assert "(outer)" not in output


def test_phase_close_wide_mode_shows_inner_qualifier() -> None:
    """In wide mode, show_phase_close_banner appends '(inner)' to analysis cycle label."""
    ctx = _make_ctx("wide")
    exit_model = PhaseExitModel(
        phase_name="development_analysis",
        inner_analysis=2,
        inner_analysis_cap=4,
    )
    show_phase_close_banner(exit_model, display_context=ctx)
    output = _export(ctx)
    assert "Analysis 2/4" in output
    assert "(inner)" in output


def test_phase_close_debug_breadcrumbs_wide_mode() -> None:
    """In wide mode, show_phase_close_banner shows debug breadcrumbs when set."""
    ctx = _make_ctx("wide")
    exit_model = PhaseExitModel(
        phase_name="development",
        exit_trigger="timeout",
        waiting_status_line="waiting for tool response",
        last_failure_category="environmental",
    )
    show_phase_close_banner(exit_model, display_context=ctx)
    output = _export(ctx)
    assert "debug:" in output
    assert "waiting: waiting for tool response" in output
    assert "failure: environmental" in output


def test_phase_close_debug_breadcrumbs_compact_mode() -> None:
    """In compact mode, show_phase_close_banner still shows debug breadcrumbs when set."""
    ctx = _make_ctx("compact")
    exit_model = PhaseExitModel(
        phase_name="development",
        exit_trigger="timeout",
        waiting_status_line="waiting for something",
        last_failure_category="agent",
    )
    show_phase_close_banner(exit_model, display_context=ctx)
    output = _export(ctx)
    assert "debug:" in output
    assert "waiting: waiting for something" in output
    assert "failure: agent" in output


def test_phase_close_medium_mode_shows_outer_qualifier() -> None:
    """In medium mode, show_phase_close_banner appends '(outer)' to dev cycle label."""
    ctx = _make_ctx("medium")
    exit_model = PhaseExitModel(
        phase_name="development",
        outer_dev_iteration=2,
        outer_dev_cap=4,
    )
    show_phase_close_banner(exit_model, display_context=ctx)
    output = _export(ctx)
    assert "Dev 2/4" in output
    assert "(outer)" in output


def test_phase_close_stats_line_medium_mode() -> None:
    """In medium mode, show_phase_close_banner emits a stats line when activity > 0."""
    ctx = _make_ctx("medium")
    exit_model = PhaseExitModel(
        phase_name="development",
        content_blocks=3,
        thinking_blocks=1,
        tool_calls=7,
        errors=0,
    )
    show_phase_close_banner(exit_model, display_context=ctx)
    output = _export(ctx)
    assert "stats:" in output
    assert "content=3" in output
    assert "thinking=1" in output
    assert "tools=7" in output


def test_phase_close_stats_line_omitted_when_all_zero() -> None:
    """show_phase_close_banner omits the stats line when all counters are zero."""
    ctx = _make_ctx("medium")
    exit_model = PhaseExitModel(phase_name="development")
    show_phase_close_banner(exit_model, display_context=ctx)
    output = _export(ctx)
    assert "stats:" not in output


def test_phase_close_stats_line_compact_mode_omitted() -> None:
    """In compact mode, stats line is never shown even with non-zero counters."""
    ctx = _make_ctx("compact")
    exit_model = PhaseExitModel(
        phase_name="development",
        content_blocks=5,
        tool_calls=3,
    )
    show_phase_close_banner(exit_model, display_context=ctx)
    output = _export(ctx)
    assert "stats:" not in output


def test_phase_close_stats_line_errors_shown() -> None:
    """When errors > 0, stats line shows errors in medium/wide mode."""
    ctx = _make_ctx("wide")
    exit_model = PhaseExitModel(
        phase_name="development",
        content_blocks=2,
        tool_calls=1,
        errors=3,
    )
    show_phase_close_banner(exit_model, display_context=ctx)
    output = _export(ctx)
    assert "stats:" in output
    assert "errors=3" in output


def test_phase_start_wide_mode_shows_rule_separator() -> None:
    """In wide mode, show_phase_start_from_entry emits a Rule separator line."""
    ctx = _make_ctx("wide")
    entry = PhaseEntryModel(phase_name="development")
    show_phase_start_from_entry(entry, display_context=ctx)
    output = _export(ctx)
    rule_lines = [ln for ln in output.split("\n") if "─" in ln or "━" in ln]
    assert len(rule_lines) >= _WIDE_PHASE_START_RULES, (
        f"Expected at least {_WIDE_PHASE_START_RULES} rule line(s), got {len(rule_lines)}"
    )


def test_phase_start_compact_mode_no_rule_separator() -> None:
    """In compact mode, show_phase_start_from_entry emits no Rule separator."""
    ctx = _make_ctx("compact")
    entry = PhaseEntryModel(phase_name="development")
    show_phase_start_from_entry(entry, display_context=ctx)
    output = _export(ctx)
    rule_lines = [ln for ln in output.split("\n") if "─" in ln or "━" in ln]
    assert len(rule_lines) == 0, f"Compact mode must not emit rule lines: {rule_lines}"


def test_phase_start_wide_mode_agent_on_separate_line() -> None:
    """In wide mode, agent name appears on its own indented line."""
    ctx = _make_ctx("wide")
    entry = PhaseEntryModel(phase_name="development", agent_name="claude-opus")
    show_phase_start_from_entry(entry, display_context=ctx)
    output = _export(ctx)
    assert "agent: claude-opus" in output


def test_phase_start_medium_mode_agent_on_banner_line() -> None:
    """In medium mode, agent name stays on the banner line (not separate)."""
    ctx = _make_ctx("medium")
    entry = PhaseEntryModel(phase_name="development", agent_name="claude-opus")
    show_phase_start_from_entry(entry, display_context=ctx)
    output = _export(ctx)
    assert "agent=claude-opus" in output


# --- Artifact outcome in phase-close banner ---


def test_phase_close_banner_shows_artifact_outcome_medium_mode() -> None:
    """In medium mode, show_phase_close_banner shows artifact_outcome as a ↳ artifact: line."""
    ctx = _make_ctx("medium")
    exit_model = PhaseExitModel(
        phase_name="planning",
        exit_trigger="produced",
        artifact_outcome="plan: 5 step(s), 2 risk(s)",
    )
    show_phase_close_banner(exit_model, display_context=ctx)
    output = _export(ctx)
    assert "artifact:" in output
    assert "plan: 5 step(s), 2 risk(s)" in output


def test_phase_close_banner_shows_artifact_outcome_wide_mode() -> None:
    """In wide mode, show_phase_close_banner shows artifact_outcome as a ↳ artifact: line."""
    ctx = _make_ctx("wide")
    exit_model = PhaseExitModel(
        phase_name="development",
        exit_trigger="produced",
        artifact_outcome="result produced",
    )
    show_phase_close_banner(exit_model, display_context=ctx)
    output = _export(ctx)
    assert "artifact:" in output
    assert "result produced" in output


def test_phase_close_banner_omits_artifact_outcome_when_empty() -> None:
    """show_phase_close_banner must not emit ↳ artifact: line when artifact_outcome is empty."""
    ctx = _make_ctx("wide")
    exit_model = PhaseExitModel(phase_name="development", artifact_outcome="")
    show_phase_close_banner(exit_model, display_context=ctx)
    output = _export(ctx)
    assert "artifact:" not in output


def test_phase_close_banner_artifact_outcome_omitted_compact_mode() -> None:
    """In compact mode, artifact_outcome secondary line is not shown (too noisy)."""
    ctx = _make_ctx("compact")
    exit_model = PhaseExitModel(
        phase_name="planning",
        exit_trigger="produced",
        artifact_outcome="plan: 3 step(s)",
    )
    show_phase_close_banner(exit_model, display_context=ctx)
    output = _export(ctx)
    assert "artifact:" not in output
