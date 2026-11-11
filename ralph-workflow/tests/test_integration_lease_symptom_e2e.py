from __future__ import annotations

import fcntl
from pathlib import Path

import pytest

from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
from ralph.pipeline.auto_integrate_transaction import integration_transaction
from ralph.pipeline.lease_outcome_kind import LeaseOutcomeKind
from ralph.pipeline.lease_unknown_reason import LeaseUnknownReason
from ralph.workspace.scope import WorkspaceScope


class _GitDirProbeFailure:
    returncode = 1
    stdout = ""
    stderr = "fatal: unable to access '.git': Transport endpoint is not connected\n"


def _install_rev_parse_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_transaction.run_git",
        lambda *_args, **_kwargs: _GitDirProbeFailure(),
    )


def _git_dir_with_head(root: Path) -> None:
    git_dir = root / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")


def test_environment_failure_is_unknown_never_busy_no_agent_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _git_dir_with_head(tmp_path)
    _install_rev_parse_failure(monkeypatch)

    dispatched: list[str] = []
    monkeypatch.setattr(
        "ralph.pipeline.conflict_resolution.session.invoke_resolution_agent",
        lambda *args, **kwargs: dispatched.append(str(args)) or True,
    )

    with integration_transaction(tmp_path) as outcome:
        assert outcome.kind is LeaseOutcomeKind.UNKNOWN
        assert outcome.reason is LeaseUnknownReason.GIT_DIR_UNRESOLVED
        line = outcome.describe("worktree busy; retrying")
        assert "worktree busy" not in line
        assert "GIT_DIR_UNRESOLVED" in line
        assert "without dispatching" in line
    assert dispatched == []


def test_persistent_unknown_routes_recovery_to_retained_retry_not_busy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _git_dir_with_head(tmp_path)
    _install_rev_parse_failure(monkeypatch)

    state = recover_incomplete_integration(WorkspaceScope(tmp_path))
    assert state is not None
    assert state.last_reason is not None
    assert "worktree busy" not in state.last_reason
    assert "GIT_DIR_UNRESOLVED" in state.last_reason


def test_live_holder_is_contention_and_releases_after_death(
    tmp_git_repo: Path,
) -> None:
    lock_path = tmp_git_repo / ".git" / "ralph-auto-integrate.lock"
    with lock_path.open("a+", encoding="utf-8") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with integration_transaction(tmp_git_repo) as outcome:
            assert outcome.kind is LeaseOutcomeKind.HELD_BY_LIVE_OWNER
            assert "busy" in outcome.describe("worktree busy; retrying")
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
    with integration_transaction(tmp_git_repo) as outcome:
        assert outcome.acquired
    assert lock_path.exists()


def test_unknown_then_recovery_lands_when_environment_heals(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    real_probe = _GitDirProbeFailure()

    def _flaky(*_args: object, **_kwargs: object) -> object:
        calls.append("probe")
        if len(calls) <= 1:
            return real_probe

        class _Healed:
            returncode = 0
            stdout = str(tmp_git_repo / ".git") + "\n"
            stderr = ""

        return _Healed()

    monkeypatch.setattr("ralph.pipeline.auto_integrate_transaction.run_git", _flaky)

    with integration_transaction(tmp_git_repo) as first:
        assert first.kind is LeaseOutcomeKind.UNKNOWN
    with integration_transaction(tmp_git_repo) as second:
        assert second.acquired
