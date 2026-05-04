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
    PhaseStartContext,
    show_phase_complete,
    show_phase_start,
    show_phase_transition,
)
from ralph.display.theme import ASCII_GLYPHS, UNICODE_GLYPHS
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy, RecoveryPolicy

_WIDE_EXPECTED_RULES = 2


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


def test_phase_complete_uses_unicode_checkmark_by_default() -> None:
    """Without ASCII override, show_phase_complete uses a Unicode checkmark."""
    ctx = _make_ctx("wide", ascii_mode=False)
    show_phase_complete("review", display_context=ctx)
    output = _export(ctx)
    assert UNICODE_GLYPHS["success"] in output


def test_phase_complete_uses_ok_badge_in_ascii_mode() -> None:
    """With RALPH_FORCE_ASCII=1, show_phase_complete uses '[OK]'."""
    ctx = _make_ctx("wide", ascii_mode=True)
    show_phase_complete("review", display_context=ctx)
    output = _export(ctx)
    assert ASCII_GLYPHS["success"] in output
    assert UNICODE_GLYPHS["success"] not in output


# --- Phase-start ordering tests ---

def test_phase_start_outer_dev_appears_before_analysis_iteration() -> None:
    """outer_dev ([Dev #N]) must appear before analysis_iteration in phase-start output."""
    ctx = _make_ctx("wide")
    start_ctx = PhaseStartContext(
        outer_iteration=3,
        analysis_iteration=1,
        max_analysis_iterations=5,
        phase_name="analysis",
    )
    show_phase_start("analysis", display_context=ctx, ctx=start_ctx)
    output = _export(ctx)
    dev_pos = output.find("[Dev #3]")
    analysis_pos = output.find("analysis 2/5")
    assert dev_pos != -1, f"Expected '[Dev #3]' in output, got: {output!r}"
    assert analysis_pos != -1, f"Expected 'analysis 2/5' in output, got: {output!r}"
    assert dev_pos < analysis_pos, (
        f"[Dev #3] must appear before analysis 2/5, "
        f"but dev_pos={dev_pos} analysis_pos={analysis_pos}"
    )


def test_phase_start_no_outer_dev_only_analysis_iteration() -> None:
    """Without outer_dev, analysis_iteration still renders with inner_analysis style."""
    ctx = _make_ctx("wide")
    start_ctx = PhaseStartContext(
        analysis_iteration=0,
        max_analysis_iterations=3,
        phase_name="analysis",
    )
    show_phase_start("analysis", display_context=ctx, ctx=start_ctx)
    output = _export(ctx)
    assert "analysis 1/3" in output
    assert "[Dev #" not in output
