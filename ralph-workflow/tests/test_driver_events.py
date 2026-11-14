from __future__ import annotations

from typing import TYPE_CHECKING

import ralph.pipeline.conflict_resolution._driver_events as driver_events_module
from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline import IntegrationStatus
from ralph.pipeline.conflict_resolution import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution import (
    driver as driver_module,
)
from ralph.pipeline.conflict_resolution._driver_events import (
    announce_declared_decisions,
    emit_attempt_outcome,
    emit_success,
)
from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
from ralph.pipeline.resolution_outcome import ResolvedOutcome, RetryLaterOutcome

if TYPE_CHECKING:
    import pytest


class _FakeDisplay(ParallelDisplay):
    def __init__(self) -> None:
        self.warnings: list[tuple[str, str, str]] = []

    def emit_warn_line(self, unit_id: str, tag: str, message: str) -> None:
        self.warnings.append((unit_id, tag, message))


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
    announce_declared_decisions(
        display,
        IntegrationStatus("DECLARED_DECISION", (), "", True),
    )
    assert display.warnings == []


def test_announce_declared_decisions_emits_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    lines: list[str] = []

    def _hook(_d: ParallelDisplay | None, line: str) -> None:
        _record(lines, _d, line)

    monkeypatch.setattr(driver_module, "emit_conflict_phase_line", _hook)
    announce_declared_decisions(
        None,
        IntegrationStatus(
            "DECLARED_DECISION",
            (("a.txt", "marker-less declaration"), ("b.txt", "marker-less declaration")),
            "",
            True,
        ),
    )
    assert len(lines) == 1
    assert "a.txt" in lines[0]
    assert "b.txt" in lines[0]
    assert "a.txt: marker-less declaration" in lines[0]
    assert "b.txt: marker-less declaration" in lines[0]


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
    outcome = ResolvedOutcome()
    emit_attempt_outcome(display, outcome)
    assert display.warnings == []


def test_emit_attempt_outcome_emits_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    lines: list[str] = []

    def _hook(_d: ParallelDisplay | None, line: str) -> None:
        _record(lines, _d, line)

    monkeypatch.setattr(driver_module, "emit_conflict_phase_line", _hook)
    outcome = RetryLaterOutcome(on_change="file.py changed")
    emit_attempt_outcome(None, outcome)
    assert len(lines) == 1
    assert lines == ["retry later: on_change=file.py changed"]
