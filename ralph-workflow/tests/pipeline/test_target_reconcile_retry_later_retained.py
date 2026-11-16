from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.git.subprocess_runner import GitRunOptions, GitRunResult, run_git
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline._remote_reconcile_recovery import _continue_target_reconciliation
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.conflict_resolution import ResolutionTerminationReason
from ralph.pipeline.rebase_state import RebaseState, StartupRetentionReason
from ralph.pipeline.resolution_outcome import RetryLaterOutcome

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution import RebaseStop

pytestmark = pytest.mark.timeout_seconds(10)

_OPTIONS = GitRunOptions(timeout=5)
_ON_CHANGE_MESSAGE = (
    "all resolver agents are spent/unavailable; conflict on shared.txt "
    "needs operator action: git add -- shared.txt"
)


def _git(root: Path, *args: str, label: str = "target-reconcile-retry-later") -> GitRunResult:
    return run_git(args, cwd=root, label=label, options=_OPTIONS)


def _commit(root: Path, content: str, message: str) -> str:
    (root / "shared.txt").write_text(content, encoding="utf-8")
    assert _git(root, "add", "shared.txt").returncode == 0
    assert _git(root, "commit", "-m", message).returncode == 0
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _conflicted_repo(root: Path) -> tuple[str, str, str]:
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0
    assert _git(root, "config", "commit.gpgsign", "false").returncode == 0
    _commit(root, "base\n", "base")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "feature-1\n", "feature 1")
    assert _git(root, "switch", "main").returncode == 0
    target = _commit(root, "target\n", "target")
    assert _git(root, "switch", "feature").returncode == 0
    assert _git(root, "rebase", "main").returncode != 0
    stop = _git(root, "rev-parse", "REBASE_HEAD").stdout.strip()
    return feature, target, stop


def _target_reconcile_record(
    root: Path,
    feature: str,
    target: str,
    stop: str,
) -> IntegrationRecord:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feature,
        pre_target_sha=target,
        resolving_rebase=True,
        resolving_stop_sha=stop,
        owning_worktree=str(root),
        operation_kind="target_reconcile",
    )
    write_record(root, record)
    return record


def test_target_reconcile_retry_later_outcome_retains_record(tmp_path: Path) -> None:
    owner = tmp_path / "repo"
    owner.mkdir()
    feature, target, stop = _conflicted_repo(owner)
    record = _target_reconcile_record(owner, feature, target, stop)

    consulted_stops: list[str] = []

    def _resolver(
        resolver_root: Path,
        resolver_target: str,
        resolver_stop: RebaseStop,
    ) -> RetryLaterOutcome:
        assert resolver_root == owner
        assert resolver_target == target
        consulted_stops.append(resolver_stop.sha)
        return RetryLaterOutcome(
            on_change=_ON_CHANGE_MESSAGE,
            terminal_reason=ResolutionTerminationReason.CANDIDATE_EXHAUSTED,
            failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
            unresolved_paths=("shared.txt",),
        )

    state = _continue_target_reconciliation(
        owner,
        owner,
        record,
        None,
        None,
        _resolver,
    )

    assert consulted_stops == [stop]
    assert isinstance(state, RebaseState)
    assert state.last_outcome is RebaseOutcomeKind.SKIPPED
    assert state.last_target == "main"
    assert state.last_reason == _ON_CHANGE_MESSAGE
    assert state.recovery_record_retained is True
    assert state.recovery_record_retained_reason is StartupRetentionReason.REMOTE_RECONCILE_RETAINED
    assert state.fast_forwarded is False

    saved = read_record(owner)
    assert saved is not None
    assert not saved.is_integrated
