"""Deterministic guards for the phase-boundary cleanliness contract.

The real-git proof of this behaviour lives in
``tests/test_auto_integrate_worktree_sync.py``, which is marked
``subprocess_e2e`` -- and ``make verify`` selects
``-m 'not subprocess_e2e and not smoke'``. A contract that is only
checked by an opt-in suite rots, so the two invariants an operator
actually depends on are pinned here as fast, subprocess-free tests
that the default gate runs.

Both invariants exist because the phase boundary is the ONLY seam
that carries another agent's landing to an agent that is not
committing right now:

1. The cleanliness probe must ignore untracked files, or one stray
   scratch file silently disables cross-agent synchronisation.
2. A deferral that suppressed real catch-up work must be recorded,
   or the operator cannot tell a working feature from a dead one.

Every filesystem touch uses the ``tmp_path`` fixture, which
``ralph.testing.audit_test_policy._is_using_tmp_path`` accepts, so this
module needs no ``_IO_ALLOWLIST`` entry.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import ralph.pipeline.auto_integrate as ai
from ralph.config.models import UnifiedConfig
from ralph.pipeline.auto_integrate_boundary_refresh import BoundaryRefreshThrottle
from ralph.pipeline.auto_integrate_sync import REFRESH_REFRESHED
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    import pytest

_HEAD_SHA = "a" * 40
_TARGET_SHA = "b" * 40


@dataclass(frozen=True)
class _StubGitResult:
    """Minimal stand-in for the ``run_git`` completed-process result."""

    returncode: int
    stdout: str


def _record_status_argv(
    monkeypatch: pytest.MonkeyPatch, *, returncode: int
) -> list[tuple[str, ...]]:
    """Replace ``ai.run_git`` with a recorder returning a fixed exit code."""
    seen: list[tuple[str, ...]] = []

    def _fake_run_git(
        argv: tuple[str, ...], *, cwd: Path, label: str
    ) -> _StubGitResult:
        seen.append(tuple(argv))
        return _StubGitResult(returncode=returncode, stdout="")

    monkeypatch.setattr(ai, "run_git", _fake_run_git)
    return seen


def test_boundary_cleanliness_probe_ignores_untracked_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-01: the probe asks git to ignore untracked files, and fails closed.

    ``--untracked-files=no`` is the same definition of "clean" that
    ``rebase_preconditions._ensure_clean_worktree`` already uses for the
    commit seam. Dropping the flag here re-creates the run-wide outage
    that docstring records: one scratch file disabling integration for
    the rest of the run.
    """
    seen = _record_status_argv(monkeypatch, returncode=0)

    assert ai._worktree_is_clean(tmp_path) is True
    assert seen == [("status", "--porcelain", "--untracked-files=no")]

    # Any git failure still counts as "not clean" (fail closed).
    _record_status_argv(monkeypatch, returncode=1)
    assert ai._worktree_is_clean(tmp_path) is False


def _dirty_boundary_config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": "main",
                "auto_integrate_fetch_enabled": False,
            }
        }
    )


def test_dirty_boundary_records_a_skip_when_the_target_is_ahead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-02: a suppressed cross-agent catch-up is diagnosable, not silent.

    Returning ``None`` for every dirty boundary meant the one case the
    operator needed to see -- "another agent landed and I could not pick
    it up" -- looked exactly like the eleven-per-cycle routine case.
    Recording is gated on the target genuinely carrying commits this
    checkout lacks, so routine boundaries add no run-state noise.
    """
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(ai, "resolve_integration_target", lambda _config, _root: "main")
    monkeypatch.setattr(ai, "_worktree_is_clean", lambda _root: False)
    monkeypatch.setattr(ai, "get_head_sha", lambda _root: _HEAD_SHA)
    monkeypatch.setattr(ai, "branch_sha", lambda _root, _name: _TARGET_SHA)
    monkeypatch.setattr(ai, "is_ancestor", lambda _root, _a, _b: False)

    config = _dirty_boundary_config()
    scope = WorkspaceScope(tmp_path)

    result = ai.auto_integrate_on_phase_transition(config, scope, RebaseState())

    assert result is not None, (
        "a dirty boundary that suppressed real catch-up work must be recorded"
    )
    assert result.last_action == "skipped"
    assert result.last_target == "main"
    assert result.last_reason is not None
    assert "worktree not clean" in result.last_reason

    # The target is already contained in HEAD: nothing was lost, so the
    # routine boundary stays silent.
    monkeypatch.setattr(ai, "is_ancestor", lambda _root, _a, _b: True)
    assert ai.auto_integrate_on_phase_transition(config, scope, RebaseState()) is None


def test_dirty_boundary_regression_suppressed_divergence_forces_a_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-06: an armed throttle cannot leave a catch-up verdict stale."""
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(ai, "resolve_integration_target", lambda _config, _root: "main")
    monkeypatch.setattr(ai, "_worktree_is_clean", lambda _root: False)
    monkeypatch.setattr(ai, "get_head_sha", lambda _root: _HEAD_SHA)
    monkeypatch.setattr(ai, "branch_sha", lambda _root, _name: _TARGET_SHA)
    monkeypatch.setattr(ai, "is_ancestor", lambda _root, _a, _b: False)

    throttle = BoundaryRefreshThrottle(min_interval_seconds=30.0)
    throttle.record_outcome(tmp_path, "main", REFRESH_REFRESHED)
    monkeypatch.setattr(ai, "BOUNDARY_REFRESH_THROTTLE", throttle)
    refresh_calls: list[str] = []

    def _forced_refresh(
        _config: UnifiedConfig, _root: Path, target: str
    ) -> str:
        refresh_calls.append(target)
        return REFRESH_REFRESHED

    monkeypatch.setattr(ai, "_refresh_target", _forced_refresh)

    result = ai.auto_integrate_on_phase_transition(
        _dirty_boundary_config(), WorkspaceScope(tmp_path), RebaseState()
    )

    assert refresh_calls == ["main"]
    assert result is not None
    assert result.last_refresh == REFRESH_REFRESHED
