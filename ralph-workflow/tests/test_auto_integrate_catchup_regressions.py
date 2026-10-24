"""Regression coverage for the 2026-10-24 background catch-up incident.

The background ticker must never mutate a worktree while an agent lease is
held, must charge every fast-forward attempt to the repository-wide durable
ledger, and must quarantine a repeated same-state timeout instead of
retrying the identical merge forever.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.config.models import UnifiedConfig
from ralph.pipeline import auto_integrate_catchup as catchup
from ralph.pipeline.integration_attempt_ledger import AttemptLedger, repository_ledger_path
from ralph.pipeline.integration_decision import EscalationRung
from ralph.process.manager import ProcessTerminationError

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class _RecordingClock:
    """Injected clock that records logical sleeps without waiting."""

    current: float = 100.0
    sleeps: list[float] = field(default_factory=list)

    def now(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += seconds


@dataclass(frozen=True)
class _FakeLease:
    """Lease stand-in for the catch-up mutation gate."""

    name: str = "auto-integrate-catchup"


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": "main",
                "auto_integrate_remote_enabled": False,
            }
        }
    )


def _ledger(tmp_path: Path) -> tuple[AttemptLedger, Path]:
    common_dir = tmp_path / "common-git"
    common_dir.mkdir()
    ledger = AttemptLedger(tmp_path, common_dir=common_dir)
    return ledger, repository_ledger_path(common_dir)


def _open_gates(
    monkeypatch: pytest.MonkeyPatch,
    *,
    lease_held: bool = False,
    merge: Callable[[Path, str], bool] | None = None,
    target_sha: str = "b" * 40,
) -> list[tuple[Path, str]]:
    """Open every catch-up gate without running Git."""

    calls: list[tuple[Path, str]] = []

    def _merge(root: Path, sha: str) -> bool:
        calls.append((root, sha))
        return True if merge is None else merge(root, sha)

    def _resolve(*_args: object, **_kwargs: object) -> str:
        return "main"

    def _current(*_args: object, **_kwargs: object) -> str:
        return "feature"

    def _clean(*_args: object, **_kwargs: object) -> bool:
        return True

    def _observe(_root: Path, name: str) -> tuple[str, bool]:
        return (target_sha, True) if name == "main" else ("a" * 40, True)

    def _ancestor(*_args: object, **_kwargs: object) -> bool:
        return True

    def _safe(*_args: object, **_kwargs: object) -> bool:
        return True

    def _held(*_args: object, **_kwargs: object) -> bool:
        return lease_held

    def _acquire(*_args: object, **_kwargs: object) -> _FakeLease | None:
        return None if lease_held else _FakeLease()

    def _release(_lease: object) -> None:
        return None

    monkeypatch.setattr(catchup, "resolve_integration_target", _resolve)
    monkeypatch.setattr(catchup, "_current_branch_name", _current)
    monkeypatch.setattr(catchup, "_worktree_is_clean", _clean)
    monkeypatch.setattr(catchup, "observe_branch_sha", _observe)
    monkeypatch.setattr(catchup, "is_ancestor", _ancestor)
    monkeypatch.setattr(catchup, "_still_safe_to_merge", _safe)
    monkeypatch.setattr(catchup, "fast_forward_via_worktree", _merge)
    monkeypatch.setattr(catchup, "is_lease_held", _held)
    monkeypatch.setattr(catchup, "try_acquire_lease", _acquire)
    monkeypatch.setattr(catchup, "end_agent_lease", _release)
    return calls


def test_catchup_regression_agent_lease_refuses_merge_without_charge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A held agent lease blocks the tick before any merge or ledger charge."""

    calls = _open_gates(monkeypatch, lease_held=True)
    ledger, ledger_path = _ledger(tmp_path)

    outcome = catchup.attempt_catchup_fast_forward(
        _config(),
        tmp_path,
        ledger=ledger,
        clock=_RecordingClock(),
    )

    assert outcome == "refused: agent is currently active"
    assert calls == []
    assert ledger.read().repeat_counts == {}
    assert not ledger_path.exists()


class _FakeRepo:
    """GitPython repository double exposing only the common directory."""

    def __init__(
        self,
        _root: Path,
        *,
        search_parent_directories: bool = False,
    ) -> None:
        del search_parent_directories
        self.common_dir = str(_root / "common-git")

    def close(self) -> None:
        """Match the repository close seam used by production code."""


def test_catchup_regression_default_ledger_is_repository_wide(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The production ledger path is anchored to the repository common directory."""

    common_dir = tmp_path / "common-git"
    common_dir.mkdir()
    _open_gates(monkeypatch)
    monkeypatch.setattr(catchup, "Repo", _FakeRepo)

    outcome = catchup.attempt_catchup_fast_forward(
        _config(),
        tmp_path,
        clock=_RecordingClock(),
    )

    assert outcome == catchup.CATCHUP_FAST_FORWARDED
    assert (common_dir / "ralph" / "integration_attempt_ledger.json").exists()


def test_catchup_regression_successful_merge_is_charged_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The durable repository-wide ledger records exactly one merge attempt."""

    calls = _open_gates(monkeypatch)
    ledger, ledger_path = _ledger(tmp_path)

    outcome = catchup.attempt_catchup_fast_forward(
        _config(),
        tmp_path,
        ledger=ledger,
        clock=_RecordingClock(),
    )

    assert outcome == catchup.CATCHUP_FAST_FORWARDED
    assert calls == [(tmp_path, "b" * 40)]
    assert sum(ledger.read().repeat_counts.values()) == 1
    assert ledger_path.exists()


def test_catchup_regression_timeout_is_named_charged_and_backed_off(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A timeout is unknown evidence, not a block, and spends one durable attempt."""

    def _timeout(_root: Path, _sha: str) -> bool:
        raise subprocess.TimeoutExpired(cmd=["git", "merge"], timeout=120.0)

    _open_gates(monkeypatch, merge=_timeout)
    ledger, _ledger_path = _ledger(tmp_path)
    clock = _RecordingClock()

    outcome = catchup.attempt_catchup_fast_forward(
        _config(),
        tmp_path,
        ledger=ledger,
        clock=clock,
    )

    assert "timed out" in outcome
    assert sum(ledger.read().repeat_counts.values()) == 1
    assert clock.sleeps == [0.5]


def test_catchup_regression_repeated_timeout_escalates_without_third_merge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Two identical timeouts quarantine the third identical merge and advance the rung."""

    def _timeout(_root: Path, _sha: str) -> bool:
        raise subprocess.TimeoutExpired(cmd=["git", "merge"], timeout=120.0)

    calls = _open_gates(monkeypatch, merge=_timeout)
    ledger, _ledger_path = _ledger(tmp_path)
    clock = _RecordingClock()

    first = catchup.attempt_catchup_fast_forward(
        _config(), tmp_path, ledger=ledger, clock=clock
    )
    second = catchup.attempt_catchup_fast_forward(
        _config(), tmp_path, ledger=ledger, clock=clock
    )
    third = catchup.attempt_catchup_fast_forward(
        _config(), tmp_path, ledger=ledger, clock=clock
    )

    assert "timed out" in first
    assert "timed out" in second
    assert "dispatch_resolver" in third
    assert len(calls) == 2
    state = ledger.read()
    assert sum(state.repeat_counts.values()) == 2
    assert state.rung is EscalationRung.DISPATCH_RESOLVER
    assert clock.sleeps == [0.5, 1.0]


def test_catchup_regression_changed_observation_gets_a_fresh_attempt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A changed target tip is a new state and may use a fresh attempt budget."""

    target_sha = "b" * 40

    def _timeout(_root: Path, _sha: str) -> bool:
        raise subprocess.TimeoutExpired(cmd=["git", "merge"], timeout=120.0)

    calls = _open_gates(monkeypatch, merge=_timeout, target_sha=target_sha)
    ledger, _ledger_path = _ledger(tmp_path)
    clock = _RecordingClock()

    for _ in range(2):
        catchup.attempt_catchup_fast_forward(
            _config(), tmp_path, ledger=ledger, clock=clock
        )

    def _changed_observation(_root: Path, name: str) -> tuple[str, bool]:
        return ("c" * 40, True) if name == "main" else ("a" * 40, True)

    monkeypatch.setattr(catchup, "observe_branch_sha", _changed_observation)
    outcome = catchup.attempt_catchup_fast_forward(
        _config(), tmp_path, ledger=ledger, clock=clock
    )

    assert "timed out" in outcome
    assert len(calls) == 3
    assert len(ledger.read().repeat_counts) == 2


@pytest.mark.parametrize(
    ("failure_kind", "expected_reason"),
    [
        ("definite", "definitely failed"),
        ("killed", "killed"),
        ("unknown", "unknown"),
    ],
)
def test_catchup_regression_git_failure_outcomes_are_distinguished(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_kind: str,
    expected_reason: str,
) -> None:
    """Definite failure, kill, and unknown remain distinct named outcomes."""

    def _fail(_root: Path, _sha: str) -> bool:
        if failure_kind == "killed":
            raise ProcessTerminationError(123, 123)
        if failure_kind == "unknown":
            raise RuntimeError("environmental failure")
        return False

    _open_gates(monkeypatch, merge=_fail)
    ledger, _ledger_path = _ledger(tmp_path)

    outcome = catchup.attempt_catchup_fast_forward(
        _config(),
        tmp_path,
        ledger=ledger,
        clock=_RecordingClock(),
    )

    assert expected_reason in outcome
    assert sum(ledger.read().repeat_counts.values()) == 1


def test_catchup_regression_tick_exception_log_names_timeout_without_blanket_retry_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The worker classifies an escaped timeout instead of logging a blanket retry."""

    messages: list[str] = []

    def _warning(message: str, *args: object) -> None:
        messages.append(message.format(*args) if args else message)

    def _timeout_tick() -> str:
        raise subprocess.TimeoutExpired(cmd=["git", "merge"], timeout=120.0)

    monkeypatch.setattr(logger, "warning", _warning)
    worker = catchup.AutoIntegrateCatchupWorker(
        _config(),
        tmp_path,
        tick=_timeout_tick,
    )

    worker._tick_once()

    assert len(messages) == 1
    assert "timed out" in messages[0]
    assert "will retry next interval" not in messages[0]
