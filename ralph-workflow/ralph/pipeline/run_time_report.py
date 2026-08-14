"""Build and persist the bounded, comparable end-of-run time report."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from loguru import logger

from ralph.mcp.artifacts.canonical_submit import submit_artifact_canonical
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.tools.artifact import ArtifactHandlerDeps

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import CycleTimeboxPolicy

_REPORTING_BUDGET_CHARACTERS = 1_600
_MAX_REPORTED_PHASES = 6
_MAX_MEMORY_FINDINGS = 8
_MAX_PHASE_NAME_CHARACTERS = 80
_MAX_REDIRECT_REASON_CHARACTERS = 200
_MAX_FIXED_POINT_ELAPSED_CHARACTERS = 24
_MAX_DIRECT_COUNT_BITS = 64


def _safe_text(value: str) -> str:
    return value.replace("\n", " ")[:_MAX_PHASE_NAME_CHARACTERS]


def _safe_reason(value: str) -> str:
    """Cap a redirect reason without cutting the phase name it ends with.

    The phase-name cap is far too short for a sentence: the bundled reason runs
    to 92 characters and ends with the finalization target, so trimming at 80
    handed the operator a truncated phase name that does not exist.
    """
    return value.replace("\n", " ")[:_MAX_REDIRECT_REASON_CHARACTERS]


def _safe_item_text(value: str) -> str:
    return _safe_text(value).rstrip()


def _memory_findings(getenv: Callable[[str], str | None]) -> tuple[list[str], bool]:
    """Return the findings to report, and whether the cap dropped any."""
    findings = getenv("RALPH_RUN_TIME_REPORT_MEMORY_FINDINGS") or ""
    parsed = [
        _safe_item_text(finding) for finding in findings.splitlines() if _safe_item_text(finding)
    ]
    return parsed[:_MAX_MEMORY_FINDINGS], len(parsed) > _MAX_MEMORY_FINDINGS


def _format_elapsed(elapsed_seconds: float) -> str:
    formatted = f"{elapsed_seconds:.3f}"
    return (
        formatted
        if len(formatted) <= _MAX_FIXED_POINT_ELAPSED_CHARACTERS
        else f"{elapsed_seconds:.3g}"
    )


def _format_count(value: int) -> str:
    if value.bit_length() <= _MAX_DIRECT_COUNT_BITS:
        return str(value)
    sign = "-" if value < 0 else ""
    return f"{sign}~1e{int(value.bit_length() * 0.30103)}"


def _slowest_phases(state: PipelineState) -> tuple[list[tuple[str, str]], bool]:
    """Return the slowest phases to report, and whether the cap dropped any.

    The duration is a phase's slowest SINGLE execution, not its total across
    the run — this is the "slowest steps" question. The caller announces the
    cap: dropping phases silently reads as a complete list.
    """
    elapsed_by_phase: dict[str, int] = {}
    for timing in state.phase_timings:
        phase = _safe_text(timing.phase)
        elapsed_by_phase[phase] = max(elapsed_by_phase.get(phase, 0), timing.elapsed_seconds)
    phase_durations = [(-duration, phase) for phase, duration in elapsed_by_phase.items()]
    phase_durations.sort()
    reported = [
        (phase, _format_count(-duration))
        for duration, phase in phase_durations[:_MAX_REPORTED_PHASES]
    ]
    return reported, len(phase_durations) > _MAX_REPORTED_PHASES


def _cycle_timebox_was_used(state: PipelineState) -> bool:
    """Return whether this run ever had a cycle timer worth reporting.

    Consumed seconds alone are not the test: they are exactly zero for the
    whole first step of every cycle, so a run ending there dropped the section
    entirely while the live phase banner was concurrently showing its budget.
    """
    return bool(
        state.cycle_timebox_active
        or state.cycle_timebox_consumed_seconds > 0
        or state.cycle_timebox_redirects
        or state.cycle_timebox_redirect_reason
    )


def _cycle_consumed_text(state: PipelineState) -> str:
    """Render the consumed figure and how the cycle it belongs to ended.

    "active" is never right here: the report is written after the run is over.
    A run that exits from inside its cycle -- an exhausted recovery, an
    interrupt -- never routes out of it, so the flag is still set and the
    report described a finished run as still running.
    """
    consumed = _format_elapsed(state.cycle_timebox_consumed_seconds)
    ending = " open at exit." if state.cycle_timebox_active else " concluded."
    return f"{consumed}s;{ending}"


def _redirect_line(state: PipelineState) -> str:
    """Render the deadline-redirect line, counting redirects across the run.

    The count is printed as recorded rather than floored to one. Flooring it
    made a never-incremented counter indistinguishable from a correct one, so
    a report could claim a redirect total it had never actually counted. A
    checkpoint written before the counter existed carries a reason with no
    count, and is reported without one.
    """
    count = state.cycle_timebox_redirects
    reason = state.cycle_timebox_redirect_reason
    if reason and not count:
        return f"- [CT-2] Redirected: {_safe_reason(reason)}.\n"
    if not count:
        return ""
    detail = _safe_reason(reason) if reason else "an earlier cycle reached its deadline"
    return f"- [CT-2] Redirected cycles: {count}; {detail}.\n"


def render_run_time_report(
    *,
    state: PipelineState,
    outcome: str,
    elapsed_seconds: float,
    getenv: Callable[[str], str | None] = os.environ.get,
    cycle_timebox: CycleTimeboxPolicy | None = None,
) -> str:
    """Return the stable, bounded Markdown report for one pipeline execution."""
    safe_phase = _safe_text(state.phase)
    elapsed = max(0.0, elapsed_seconds)
    elapsed_text = _format_elapsed(elapsed)
    reported_phases, dropped_for_cap = _slowest_phases(state)
    memory_findings, memory_findings_capped = _memory_findings(getenv)
    truncated = False
    memory_findings_truncated = False
    while True:
        phase_lines = [f"- [P-1] Final phase: {safe_phase}."]
        slowest_lines: list[str] = []
        if reported_phases:
            phase_lines.extend(
                f"- [P-{index}] {phase}: {duration}s."
                for index, (phase, duration) in enumerate(reported_phases, start=2)
            )
            slowest_lines.extend(
                f"- [SS-{index}] {phase}: {duration}s."
                for index, (phase, duration) in enumerate(reported_phases, start=1)
            )
            if truncated or dropped_for_cap:
                # Announced in BOTH sections: Slowest Steps lost the same
                # entries silently, which reads as a complete list.
                notice = (
                    "Phase timing list truncated to fit the reporting budget."
                    if truncated
                    else f"Only the {_MAX_REPORTED_PHASES} slowest phases are listed."
                )
                phase_lines.append(f"- [P-8] {notice}")
                slowest_lines.append(f"- [SS-8] {notice}")
        elif state.phase_timings:
            # Recorded, then dropped to fit the budget. Saying none were
            # recorded is a different — and false — statement.
            phase_lines.append("- [P-2] All phase timings were dropped to fit the budget.")
            slowest_lines.append("- [SS-1] All phase timings were dropped to fit the budget.")
        else:
            phase_lines.append("- [P-2] No completed phase timings were recorded.")
            slowest_lines.append("- [SS-1] No completed phase timings were recorded.")
        report = (
            "---\n"
            "type: run_time_report\n"
            f"outcome: {_safe_text(outcome)}\n"
            f"elapsed_seconds: {elapsed_text}\n"
            f"final_phase: {safe_phase}\n"
            "---\n\n"
            "## Summary\n"
            f"- [SUM-1] {_safe_text(outcome)}; total wall-clock time was {elapsed_text}s.\n\n"
            "## Timing\n"
            f"- [T-1] Total wall-clock time: {elapsed_text}s.\n"
            "- [T-2] Agent-controlled time: unavailable; the runtime does not yet classify it.\n"
            "- [T-3] Imposed time: unavailable; the runtime does not yet classify waits.\n"
            "- [T-4] Imposed-time rise: unavailable until two classified reports exist.\n\n"
            "## Phases\n"
            + "\n".join(phase_lines)
            + "\n\n## Slowest Steps\n"
            + "\n".join(slowest_lines)
            + (
                "\n\n## Memory Findings\n"
                + "\n".join(
                    f"- [MF-{index}] {finding}"
                    for index, finding in enumerate(memory_findings, start=1)
                )
                + (
                    f"\n- [MF-{len(memory_findings) + 1}] Memory findings list "
                    "truncated."
                    if (memory_findings_capped or memory_findings_truncated)
                    else ""
                )
                if memory_findings
                else ""
            )
            + "\n\n## Signals\n"
            + "- [SG-1] Agent calls: "
            + _format_count(state.metrics.total_agent_calls)
            + "; retries: "
            + _format_count(state.metrics.total_retries)
            + "; continuations: "
            + _format_count(state.metrics.total_continuations)
            + "; fallbacks: "
            + _format_count(state.metrics.total_fallbacks)
            + ".\n"
            + (
                (
                    "\n## Cycle Timebox\n"
                    + (
                        f"- [CT-1] Limit: {_format_elapsed(cycle_timebox.duration_seconds)}s;"
                        + f" last cycle consumed: {_cycle_consumed_text(state)}"
                        + f" Finalization: {_safe_text(cycle_timebox.finalization_target)}.\n"
                        if cycle_timebox is not None
                        else "- [CT-1] Last cycle consumed: "
                        + f"{_cycle_consumed_text(state)}\n"
                    )
                    + _redirect_line(state)
                )
                if _cycle_timebox_was_used(state)
                else ""
            )
        )
        if len(report) <= _REPORTING_BUDGET_CHARACTERS:
            return report
        if reported_phases:
            reported_phases.pop()
            truncated = True
            continue
        if memory_findings:
            # Announced like the phase list is: a findings list that quietly
            # loses entries reads as the complete set.
            memory_findings.pop()
            memory_findings_truncated = True
            continue
        raise RuntimeError("run_time_report fixed content exceeds its reporting budget")


def emit_run_time_report(
    workspace_root: Path,
    *,
    state: PipelineState,
    outcome: str,
    elapsed_seconds: float,
    getenv: Callable[[str], str | None] = os.environ.get,
    cycle_timebox: CycleTimeboxPolicy | None = None,
) -> None:
    """Validate and persist a report without changing the pipeline's outcome."""
    __import__("ralph.mcp.artifacts.markdown.specs")

    markdown = render_run_time_report(
        state=state,
        outcome=outcome,
        elapsed_seconds=elapsed_seconds,
        getenv=getenv,
        cycle_timebox=cycle_timebox,
    )
    if len(markdown) > _REPORTING_BUDGET_CHARACTERS:
        raise ValueError("run_time_report exceeds its reporting budget")
    content, diagnostics = parse_and_validate(markdown, get_spec("run_time_report"))
    if any(diagnostic.severity == "error" for diagnostic in diagnostics):
        raise ValueError("run_time_report validation failed")
    submit_artifact_canonical(
        workspace_root,
        "run_time_report",
        content,
        markdown=markdown,
        deps=ArtifactHandlerDeps(history_enabled=True),
    )


def emit_run_time_report_safely(
    workspace_root: Path,
    *,
    state: PipelineState,
    outcome: str,
    elapsed_seconds: float,
    getenv: Callable[[str], str | None] = os.environ.get,
    cycle_timebox: CycleTimeboxPolicy | None = None,
) -> None:
    """Write the report while preserving the original pipeline exit status."""
    try:
        emit_run_time_report(
            workspace_root,
            state=state,
            outcome=outcome,
            elapsed_seconds=elapsed_seconds,
            getenv=getenv,
            cycle_timebox=cycle_timebox,
        )
    except Exception as exc:
        logger.error("run_time_report emission failed: {}", exc)


__all__ = ["emit_run_time_report_safely", "render_run_time_report"]
