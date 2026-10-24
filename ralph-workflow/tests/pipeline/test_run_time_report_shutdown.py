"""Shutdown must preserve a truthful, valid run-time report.

A run can end while integration is stalled, and the renderer can also produce
a report that the closed markdown grammar rejects. Neither condition may erase
the only end-of-run explanation. These tests drive the public shutdown seam
with frozen state and a fake artifact sink; they never signal the process or
touch the filesystem.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol
from unittest.mock import MagicMock

import pytest

import ralph.pipeline.run_time_report as run_time_report_module
from ralph.config.enums import Verbosity
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.phases.phase_timing_record import PhaseTimingRecord
from ralph.pipeline import run_loop
from ralph.pipeline import runner as runner_module
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState, RunMetrics

_STALL_SECONDS = 17_880.0


class _SubmissionSink(Protocol):
    def __call__(
        self,
        workspace_root: Path,
        artifact_type: str,
        parsed_content: dict[str, object],
        *,
        markdown: str,
        deps: object,
    ) -> None: ...


def _timing(phase: str, elapsed_seconds: int) -> PhaseTimingRecord:
    return PhaseTimingRecord(
        phase=phase,
        iteration=0,
        started_at=0.0,
        elapsed=timedelta(seconds=elapsed_seconds),
        elapsed_seconds=elapsed_seconds,
    )


def _assert_valid(report: str) -> None:
    __import__("ralph.mcp.artifacts.markdown.specs")
    _, diagnostics = parse_and_validate(report, get_spec("run_time_report"))
    errors = [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]
    assert not errors, errors


def _capture_submission(
    captured: list[str],
) -> _SubmissionSink:
    def _submit(
        workspace_root: Path,
        artifact_type: str,
        parsed_content: dict[str, object],
        *,
        markdown: str,
        deps: object,
    ) -> None:
        del workspace_root, artifact_type, parsed_content, deps
        captured.append(markdown)

    return _submit


def test_zero_agent_and_push_counts_are_a_valid_run_time_report() -> None:
    """No agent activity is evidence, not a validation error."""
    state = PipelineState(
        phase="development",
        metrics=RunMetrics(
            total_agent_calls=0,
            total_continuations=0,
            total_fallbacks=0,
            total_retries=0,
        ),
        push_count=0,
    )

    report = run_time_report_module.render_run_time_report(
        state=state,
        outcome="failed",
        elapsed_seconds=_STALL_SECONDS,
    )

    assert "Agent calls: 0; retries: 0; continuations: 0; fallbacks: 0; pushes: 0." in report
    _assert_valid(report)


def test_invalid_full_report_degrades_to_a_valid_report_with_its_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected full report is replaced, not silently discarded."""
    submitted: list[str] = []
    monkeypatch.setattr(
        run_time_report_module,
        "submit_artifact_canonical",
        _capture_submission(submitted),
    )

    def _invalid_render(**_kwargs: object) -> str:
        return (
            "---\n"
            "type: run_time_report\n"
            "outcome: failed\n"
            "elapsed_seconds: 1.000\n"
            "final_phase: development\n"
            "---\n\n"
            "## Unexpected Section\n"
            "- [X-1] Invalid full report.\n"
        )

    monkeypatch.setattr(run_time_report_module, "render_run_time_report", _invalid_render)
    state = PipelineState(
        phase="development",
        phase_timings=(_timing("development", 120),),
    )

    run_time_report_module.emit_run_time_report_safely(
        Path("/unused"),
        state=state,
        outcome="failed",
        elapsed_seconds=_STALL_SECONDS,
    )

    assert len(submitted) == 1
    report = submitted[0]
    _assert_valid(report)
    assert "run_time_report validation failed" in report
    assert "SPEC004" in report
    assert "unknown section" in report
    assert "final_phase: development" in report
    assert "Agent calls: 0; retries: 0; continuations: 0; fallbacks: 0; pushes: 0." in report
    assert "No completed phase timings were recorded." not in report
    assert "unavailable" in report


def test_interrupted_integration_stall_emits_a_valid_run_time_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The observed Ctrl+C path reaches the report with the stalled state intact."""
    submitted: list[str] = []
    monkeypatch.setattr(
        run_time_report_module,
        "submit_artifact_canonical",
        _capture_submission(submitted),
    )

    def _skip_checkpoint(_state: PipelineState, **_kwargs: object) -> None:
        return None

    def _skip_cleanup(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(runner_module, "save_checkpoint_or_log", _skip_checkpoint)
    monkeypatch.setattr(run_loop, "_cleanup_pipeline", _skip_cleanup)

    clock = iter((0.0, _STALL_SECONDS))

    def _monotonic() -> float:
        return next(clock)

    def _sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(
        run_loop,
        "time",
        SimpleNamespace(monotonic=_monotonic, sleep=_sleep),
    )
    reached = PipelineState(
        phase="development",
        metrics=RunMetrics(),
        push_count=0,
        dev_timebox_active=True,
        dev_timebox_consumed_seconds=_STALL_SECONDS,
        rebase=RebaseState(unresolved_integration_carried=True),
    )

    def _advance_then_interrupt(
        _state: PipelineState,
        ctx: run_loop._LoopContext,
        _prev_phase: str,
    ) -> tuple[PipelineState, str, int | None]:
        ctx.latest_state[:] = [reached]
        raise KeyboardInterrupt

    monkeypatch.setattr(run_loop, "_run_inner_loop", _advance_then_interrupt)

    exit_code = run_loop.run(
        MagicMock(),
        initial_state=PipelineState(phase="planning"),
        verbosity=Verbosity.QUIET,
    )

    assert exit_code == 130
    assert len(submitted) == 1
    report = submitted[0]
    _assert_valid(report)
    assert "outcome: failed" in report
    assert "final_phase: development" in report
    assert "elapsed_seconds: 17880.000" in report
    assert "Agent calls: 0; retries: 0; continuations: 0; fallbacks: 0; pushes: 0." in report
    assert "Development timebox consumed: 17880.000s" in report
    assert "Integration status: unresolved at report time." in report
    assert "SPEC004" in report
    assert "Development Timebox" in report
