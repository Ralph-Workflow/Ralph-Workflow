from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

import ralph.pipeline.auto_integrate as ai
from ralph.config.models import UnifiedConfig
from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS
from ralph.pipeline import auto_integrate_boundary as boundary
from ralph.pipeline import auto_integrate_worktree_state as ai_worktree
from ralph.pipeline.auto_integrate_boundary_refresh import BoundaryRefreshThrottle
from ralph.pipeline.auto_integrate_sync import REFRESH_REFRESHED
from ralph.pipeline.integration_resolution import TreeReadinessAssessment
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

_HEAD_SHA = "a" * 40
_TARGET_SHA = "b" * 40

_DIRTY_READINESS = TreeReadinessAssessment(
    development_dispatch_allowed=True,
    integration_ready=False,
    blocking_reasons=(
        "worktree not clean; uncommitted tracked changes deferred catch-up integration",
    ),
)


@dataclass(frozen=True)
class _StubGitResult:
    returncode: int
    stdout: str


def _record_status_argv(
    monkeypatch: pytest.MonkeyPatch, *, returncode: int
) -> list[tuple[str, ...]]:
    seen: list[tuple[str, ...]] = []

    def _fake_run_git(argv: tuple[str, ...], *, cwd: Path, label: str) -> _StubGitResult:
        seen.append(tuple(argv))
        return _StubGitResult(returncode=returncode, stdout="")

    monkeypatch.setattr(ai_worktree, "run_git", _fake_run_git)
    return seen


def test_boundary_cleanliness_probe_ignores_untracked_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _record_status_argv(monkeypatch, returncode=0)

    assert ai_worktree._worktree_is_clean(tmp_path) is True
    assert seen == [(*COMMIT_PIN_CONFIG_ARGS, "status", "--porcelain", "--untracked-files=no")]

    _record_status_argv(monkeypatch, returncode=1)
    assert ai_worktree._worktree_is_clean(tmp_path) is False


def _dirty_boundary_config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": "main",
            }
        }
    )


@pytest.mark.timeout_seconds(5)
def test_dirty_boundary_records_a_skip_when_the_target_is_ahead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(ai, "resolve_integration_target", lambda _config, _root: "main")
    monkeypatch.setattr(boundary, "assess_tree_readiness", lambda _root: _DIRTY_READINESS)
    monkeypatch.setattr(boundary, "get_head_sha", lambda _root: _HEAD_SHA)
    monkeypatch.setattr(boundary, "branch_sha", lambda _root, _name: _TARGET_SHA)
    monkeypatch.setattr(boundary, "is_ancestor", lambda _root, _a, _b: False)

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

    monkeypatch.setattr(boundary, "is_ancestor", lambda _root, _a, _b: True)
    assert ai.auto_integrate_on_phase_transition(config, scope, RebaseState()) is None


def test_dirty_clean_target_boundary_stays_silent_on_repeated_evaluations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(ai, "resolve_integration_target", lambda _config, _root: "main")
    monkeypatch.setattr(boundary, "assess_tree_readiness", lambda _root: _DIRTY_READINESS)
    monkeypatch.setattr(boundary, "get_head_sha", lambda _root: _HEAD_SHA)
    monkeypatch.setattr(boundary, "branch_sha", lambda _root, _name: _TARGET_SHA)
    monkeypatch.setattr(boundary, "is_ancestor", lambda _root, _a, _b: True)

    scope = WorkspaceScope(tmp_path)
    assert (
        ai.auto_integrate_on_phase_transition(_dirty_boundary_config(), scope, RebaseState())
        is None
    )
    assert (
        ai.auto_integrate_on_phase_transition(_dirty_boundary_config(), scope, RebaseState())
        is None
    )


def test_dirty_boundary_regression_suppressed_divergence_forces_a_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(ai, "resolve_integration_target", lambda _config, _root: "main")
    monkeypatch.setattr(boundary, "assess_tree_readiness", lambda _root: _DIRTY_READINESS)
    monkeypatch.setattr(boundary, "get_head_sha", lambda _root: _HEAD_SHA)
    monkeypatch.setattr(boundary, "branch_sha", lambda _root, _name: _TARGET_SHA)
    monkeypatch.setattr(boundary, "is_ancestor", lambda _root, _a, _b: False)

    throttle = BoundaryRefreshThrottle(min_interval_seconds=30.0)
    throttle.record_outcome(tmp_path, "main", REFRESH_REFRESHED)
    monkeypatch.setattr(boundary, "BOUNDARY_REFRESH_THROTTLE", throttle)
    refresh_calls: list[str] = []

    def _forced_refresh(_config: UnifiedConfig, _root: Path, target: str) -> str:
        refresh_calls.append(target)
        return REFRESH_REFRESHED

    monkeypatch.setattr(boundary, "_refresh_target", _forced_refresh)

    result = ai.auto_integrate_on_phase_transition(
        _dirty_boundary_config(), WorkspaceScope(tmp_path), RebaseState()
    )

    assert refresh_calls == ["main"]
    assert result is not None
    assert result.last_refresh == REFRESH_REFRESHED
