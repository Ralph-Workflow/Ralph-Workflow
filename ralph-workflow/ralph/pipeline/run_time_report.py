"""Build and persist the bounded, comparable end-of-run time report."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.mcp.artifacts.canonical_submit import submit_artifact_canonical
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.tools.artifact import ArtifactHandlerDeps

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.state import PipelineState

_REPORTING_BUDGET_CHARACTERS = 1_600
_MAX_REPORTED_PHASES = 6
_MAX_PHASE_NAME_CHARACTERS = 80


def _safe_text(value: str) -> str:
    return value.replace("\n", " ")[:_MAX_PHASE_NAME_CHARACTERS]


def _slowest_phases(state: PipelineState) -> list[tuple[str, int]]:
    elapsed_by_phase: dict[str, int] = {}
    for timing in state.phase_timings:
        phase = _safe_text(timing.phase)
        elapsed_by_phase[phase] = max(elapsed_by_phase.get(phase, 0), timing.elapsed_seconds)
    phase_durations = [(-duration, phase) for phase, duration in elapsed_by_phase.items()]
    phase_durations.sort()
    return [(phase, -duration) for duration, phase in phase_durations[:_MAX_REPORTED_PHASES]]


def render_run_time_report(*, state: PipelineState, outcome: str, elapsed_seconds: float) -> str:
    """Return the stable, bounded Markdown report for one pipeline execution."""
    safe_phase = _safe_text(state.phase)
    elapsed = max(0.0, elapsed_seconds)
    slowest_phases = _slowest_phases(state)
    phase_lines = [f"- [P-1] Final phase: {safe_phase}."]
    slowest_lines: list[str] = []
    if slowest_phases:
        phase_lines.extend(
            f"- [P-{index}] {phase}: {duration}s."
            for index, (phase, duration) in enumerate(slowest_phases, start=2)
        )
        slowest_lines.extend(
            f"- [SS-{index}] {phase}: {duration}s."
            for index, (phase, duration) in enumerate(slowest_phases, start=1)
        )
    else:
        phase_lines.append("- [P-2] No completed phase timings were recorded.")
        slowest_lines.append("- [SS-1] No completed phase timings were recorded.")
    return (
        "---\n"
        "type: run_time_report\n"
        f"outcome: {_safe_text(outcome)}\n"
        f"elapsed_seconds: {elapsed:.3f}\n"
        f"final_phase: {safe_phase}\n"
        "---\n\n"
        "## Summary\n"
        f"- [SUM-1] {_safe_text(outcome)}; total wall-clock time was {elapsed:.3f}s.\n\n"
        "## Timing\n"
        f"- [T-1] Total wall-clock time: {elapsed:.3f}s.\n"
        "- [T-2] Agent-controlled time: unavailable; the runtime does not yet classify it.\n"
        "- [T-3] Imposed time: unavailable; the runtime does not yet classify waits.\n"
        "- [T-4] Imposed-time rise: unavailable until two classified reports exist.\n\n"
        "## Phases\n"
        + "\n".join(phase_lines)
        + "\n\n## Slowest Steps\n"
        + "\n".join(slowest_lines)
        + "\n\n## Signals\n"
        + f"- [SG-1] Agent calls: {state.metrics.total_agent_calls}; retries: {state.metrics.total_retries}; continuations: {state.metrics.total_continuations}; fallbacks: {state.metrics.total_fallbacks}.\n"
    )


def emit_run_time_report(
    workspace_root: Path,
    *,
    state: PipelineState,
    outcome: str,
    elapsed_seconds: float,
) -> None:
    """Validate and persist a report without changing the pipeline's outcome."""
    __import__("ralph.mcp.artifacts.markdown.specs")

    markdown = render_run_time_report(state=state, outcome=outcome, elapsed_seconds=elapsed_seconds)
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
) -> None:
    """Write the report while preserving the original pipeline exit status."""
    try:
        emit_run_time_report(
            workspace_root,
            state=state,
            outcome=outcome,
            elapsed_seconds=elapsed_seconds,
        )
    except Exception as exc:
        logger.error("run_time_report emission failed: {}", exc)


__all__ = ["emit_run_time_report_safely", "render_run_time_report"]
