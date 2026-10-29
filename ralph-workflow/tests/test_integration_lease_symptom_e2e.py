"""E2E proof of the 2026-10-29 "integration worktree busy" symptom fix.

The observed defect: ``integration_transaction`` collapsed four unrelated
conditions into one ``acquired=False`` printed as "worktree busy", and an
rc=1 ``rev-parse`` environment failure made the run report contention that
no agent could change. These tests pin the end-to-end acceptance list:

- rc=1 ``rev-parse`` child -> ``UNKNOWN(GIT_DIR_UNRESOLVED)``; no "busy"
  wording; no agent dispatch; backoff then re-observe.
- Persistent ``UNKNOWN`` reaches the fallback ladder; a live holder in
  another worktree is genuine contention (``HELD_BY_LIVE_OWNER``); a dead
  holder self-releases with no lock-file removal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
from ralph.pipeline.auto_integrate_transaction import (
    LeaseOutcomeKind,
    LeaseUnknownReason,
    integration_transaction,
)
from ralph.workspace.scope import WorkspaceScope


class _GitDirProbeFailure:
    """Simulated rc=1 ``rev-parse --absolute-git-dir`` child process."""

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
        # The user-facing line names the typed cause and the autonomous
        # next action — never "busy", never an operator.
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
    # A persistent UNKNOWN retains the record for bounded retry/ladder
    # escalation — the run keeps going autonomously; the skip reason is
    # the typed cause, never "busy".
    assert state is not None
    assert state.last_reason is not None
    assert "worktree busy" not in state.last_reason
    assert "GIT_DIR_UNRESOLVED" in state.last_reason


def test_live_holder_is_contention_and_releases_after_death(
    tmp_git_repo: Path,
) -> None:
    from ralph.pipeline.auto_integrate_catchup_coordination import (
        _try_lock,
        _unlock,
    )

    lock_path = tmp_git_repo / ".git" / "ralph-auto-integrate.lock"
    with lock_path.open("a+", encoding="utf-8") as holder:
        assert _try_lock(holder)
        with integration_transaction(tmp_git_repo) as outcome:
            assert outcome.kind is LeaseOutcomeKind.HELD_BY_LIVE_OWNER
            assert "busy" in outcome.describe("worktree busy; retrying")
        _unlock(holder)
    with integration_transaction(tmp_git_repo) as outcome:
        assert outcome.acquired
    assert lock_path.exists()  # contention never removes the lock file


def test_unknown_then_recovery_lands_when_environment_heals(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backoff-then-re-observe: the run catches up the moment the probe heals."""
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

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_transaction.run_git", _flaky
    )

    with integration_transaction(tmp_git_repo) as first:
        assert first.kind is LeaseOutcomeKind.UNKNOWN
    with integration_transaction(tmp_git_repo) as second:
        assert second.acquired  # healed environment -> catch-up lands
