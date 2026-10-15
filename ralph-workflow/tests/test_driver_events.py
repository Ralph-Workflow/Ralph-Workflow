"""Unit tests for conflict resolution driver events helper."""

from __future__ import annotations

from typing import TYPE_CHECKING

import ralph.pipeline.conflict_resolution._driver_events as driver_events_module
from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline.conflict_resolution import driver as driver_module
from ralph.pipeline.conflict_resolution._driver_events import (
    announce_declared_decisions,
    emit_attempt_outcome,
    emit_expired_operator_cap,
    emit_success,
)
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome
from ralph.pipeline.conflict_resolution.session import ResolutionSession

if TYPE_CHECKING:
    import pytest


class _FakeDisplay(ParallelDisplay):
    def __init__(self) -> None:
        self.warnings: list[tuple[str, str, str]] = []

    def emit_warn_line(self, target: str, channel: str, message: str) -> None:
        self.warnings.append((target, channel, message))


def _record(lines: list[str], _display: ParallelDisplay | None, line: str) -> None:
    lines.append(line)


def test_emit_prefers_driver_module_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    def _hook(_display: ParallelDisplay | None, line: str) -> None:
        captured.append(f"driver:{line}")

    monkeypatch.setattr(driver_module, "emit_conflict_phase_line", _hook)
    driver_events_module._emit(None, "hello")
    assert captured == ["driver:hello"]


def test_emit_falls_back_when_driver_hook_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    display = _FakeDisplay()
    monkeypatch.delattr(driver_module, "emit_conflict_phase_line", raising=False)
    driver_events_module._emit(display, "fallback message")
    assert len(display.warnings) == 1
    assert display.warnings[0] == ("run", "rebase-conflict", "fallback message")


def test_announce_declared_decisions_empty_paths() -> None:
    display = _FakeDisplay()
    announce_declared_decisions(display, ())
    assert display.warnings == []


def test_announce_declared_decisions_emits_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    lines: list[str] = []

    def _hook(_d: ParallelDisplay | None, line: str) -> None:
        _record(lines, _d, line)

    monkeypatch.setattr(driver_module, "emit_conflict_phase_line", _hook)
    announce_declared_decisions(None, ("a.txt", "b.txt"))
    assert len(lines) == 1
    assert "a.txt, b.txt" in lines[0]


def test_emit_expired_operator_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    lines: list[str] = []

    def _hook(_d: ParallelDisplay | None, line: str) -> None:
        _record(lines, _d, line)

    monkeypatch.setattr(driver_module, "emit_conflict_phase_line", _hook)
    session = ResolutionSession()
    session.started_at = 100.0
    now = 125.0

    def clock() -> float:
        return now

    emit_expired_operator_cap(None, session, ("foo.py",), clock)
    assert session.terminal_reason is ResolutionTerminationReason.OPERATOR_CAP_REACHED
    assert session.last_duration_seconds == 25.0
    assert len(lines) == 1
    assert "OPERATOR_CAP_REACHED" in lines[0]


def test_emit_success_rebase_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    lines: list[str] = []

    def _hook(_d: ParallelDisplay | None, line: str) -> None:
        _record(lines, _d, line)

    monkeypatch.setattr(driver_module, "emit_conflict_phase_line", _hook)
    stop = RebaseStop(
        sha="abc1234",
        subject="test commit",
        conflicted_files=("foo.py",),
        stop_index=1,
        stop_cap=5,
    )
    emit_success(None, round_index=2, stop=stop)
    assert len(lines) == 1
    assert "conflicts resolved in round 2; verifying and continuing the rebase" in lines[0]


def test_emit_success_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    lines: list[str] = []

    def _hook(_d: ParallelDisplay | None, line: str) -> None:
        _record(lines, _d, line)

    monkeypatch.setattr(driver_module, "emit_conflict_phase_line", _hook)
    emit_success(None, round_index=1, stop=None)
    assert len(lines) == 1
    assert "conflicts resolved in round 1; verifying and committing the merge" in lines[0]


def test_emit_attempt_outcome_ignores_success() -> None:
    display = _FakeDisplay()
    outcome = ResolutionOutcome(
        succeeded=True,
        reason=None,
        duration_seconds=1.0,
        last_activity_kind=None,
        last_activity_at=None,
        unresolved_paths=(),
    )
    emit_attempt_outcome(display, outcome)
    assert display.warnings == []


def test_emit_attempt_outcome_emits_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    lines: list[str] = []

    def _hook(_d: ParallelDisplay | None, line: str) -> None:
        _record(lines, _d, line)

    monkeypatch.setattr(driver_module, "emit_conflict_phase_line", _hook)
    outcome = ResolutionOutcome(
        succeeded=False,
        reason=ResolutionTerminationReason.ATTEMPT_FAILED,
        duration_seconds=3.2,
        last_activity_kind="test",
        last_activity_at=123.45,
        unresolved_paths=("file.py",),
    )
    emit_attempt_outcome(None, outcome)
    assert len(lines) == 1
    assert "ATTEMPT_FAILED: duration=3.2s" in lines[0]
    assert "file.py" in lines[0]
