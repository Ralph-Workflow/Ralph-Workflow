from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from ralph.git.git_run_result import GitRunResult
from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.pipeline._auto_integrate_rebase_resolve import rebase_run_result
from ralph.pipeline._integration_continuation import (
    ExhaustedResolutionFailure,
    _promote_completed_resolution,
    _rebase_failure_transport,
)
from ralph.pipeline._integration_diagnostic_proof import (
    _check_ancestry_proof,
    _check_head_match,
    _check_parent_match,
)
from ralph.pipeline._landed_predicate import operation_landed
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    read_record,
    write_record,
)
from ralph.pipeline.conflict_resolution import ResolutionTerminationReason
from ralph.pipeline.resolution_outcome import (
    NextStrategyOutcome,
    RetryLaterOutcome,
)

pytestmark = [pytest.mark.timeout_seconds(15), pytest.mark.subprocess_e2e]
_OPTIONS = GitRunOptions(timeout=10)


def _git(root: Path, *args: str) -> GitRunResult:
    return run_git(args, cwd=root, label="u4-landed-test", options=_OPTIONS)


def _value(root: Path, *args: str) -> str:
    result = _git(root, *args)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _commit(root: Path, path: str, content: str, message: str) -> str:
    (root / path).write_text(content, encoding="utf-8")
    assert _git(root, "add", path).returncode == 0
    assert _git(root, "commit", "-m", message).returncode == 0
    return _value(root, "rev-parse", "HEAD")


def _init_repo(root: Path) -> None:
    assert _git(root, "init", "-b", "main").returncode == 0
    assert _git(root, "config", "user.name", "Test").returncode == 0
    assert _git(root, "config", "user.email", "test@example.com").returncode == 0
    assert _git(root, "config", "commit.gpgsign", "false").returncode == 0


def test_operation_landed_advanced_completion_proof(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    base = _commit(root, "file.txt", "base\n", "base commit")
    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha=base,
        pre_target_sha=base,
        integrated_feature_sha=base,
    )
    decision = operation_landed(root, record)
    assert decision.landed is True
    assert decision.proof == "advanced integrated_feature_sha"
    assert decision.observed_head == base


def test_operation_landed_rebased_range_equivalence(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    _commit(root, "shared.txt", "base\n", "base")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    _commit(root, "f1.txt", "f1\n", "feature 1")
    f2 = _commit(root, "f2.txt", "f2\n", "feature 2")
    assert _git(root, "switch", "main").returncode == 0
    t1 = _commit(root, "target.txt", "t1\n", "target 1")

    assert _git(root, "switch", "feature").returncode == 0
    assert _git(root, "rebase", "main").returncode == 0
    rebased_head = _value(root, "rev-parse", "HEAD")

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=f2,
        pre_target_sha=t1,
        resolving_rebase=True,
    )
    decision = operation_landed(root, record)
    assert decision.landed is True
    assert decision.proof == "rebased range equivalence"
    assert decision.observed_head == rebased_head


def test_operation_landed_rejects_unintegrated_feature_head(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    base = _commit(root, "base.txt", "base\n", "base")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feat = _commit(root, "feature.txt", "feature\n", "feature")

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feat,
        pre_target_sha=base,
        resolving_rebase=True,
    )
    decision = operation_landed(root, record)
    assert decision.landed is False
    assert any("unintegrated feature commit" in f for f in decision.failures)


def test_operation_landed_rejects_unrelated_target_changes(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    _commit(root, "shared.txt", "base\n", "base")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feat = _commit(root, "feature.txt", "feature\n", "feature")
    assert _git(root, "switch", "main").returncode == 0
    t1 = _commit(root, "t1.txt", "t1\n", "t1")
    _commit(root, "unrelated.txt", "unrelated\n", "unrelated extra commit")

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feat,
        pre_target_sha=t1,
        resolving_rebase=True,
    )
    decision = operation_landed(root, record)
    assert decision.landed is False


def test_promote_completed_resolution_advances_record_and_clears_continue_fields(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    _commit(root, "shared.txt", "base\n", "base")
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feat = _commit(root, "feature.txt", "feature\n", "feature")
    assert _git(root, "switch", "main").returncode == 0
    t1 = _commit(root, "target.txt", "target\n", "target commit")
    assert _git(root, "switch", "feature").returncode == 0
    assert _git(root, "rebase", "main").returncode == 0
    rebased_head = _value(root, "rev-parse", "HEAD")

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feat,
        pre_target_sha=t1,
        resolving_rebase=True,
        rebase_continue_pending=True,
        rebase_continue_head="fakehead",
        rebase_continue_stop="fakestop",
        rebase_continue_tree="faketree",
        rebase_continue_error="some error",
        owning_worktree=str(root),
    )
    write_record(root, record)

    result = _promote_completed_resolution(root, record)
    assert isinstance(result, IntegrationRecord)
    assert result.phase == "integrated"
    assert result.integrated_feature_sha == rebased_head
    assert result.resolving_rebase is False
    assert result.rebase_continue_pending is False
    assert result.rebase_continue_head is None
    assert result.rebase_continue_stop is None
    assert result.rebase_continue_tree is None
    assert result.rebase_continue_error is None

    disk_record = read_record(root)
    assert disk_record is not None
    assert disk_record.phase == "integrated"
    assert disk_record.integrated_feature_sha == rebased_head
    assert disk_record.rebase_continue_head is None


def test_diagnostic_proof_delegates_to_single_predicate(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    base = _commit(root, "base.txt", "base\n", "base")
    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha=base,
        pre_target_sha=base,
        integrated_feature_sha=base,
    )
    ok, reason = _check_head_match(root, record)
    assert ok is True
    assert "matches recorded operation" in reason

    ok_ancestry, _ = _check_ancestry_proof(root, record)
    assert ok_ancestry is True

    ok_parent, _ = _check_parent_match(root, record)
    assert ok_parent is True


def test_next_strategy_outcome_never_parks_in_rebase_resolve(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat",
        pre_target_sha="base",
        resolving_rebase=True,
    )
    outcome = NextStrategyOutcome(strategy="complete_in_place")
    result = rebase_run_result(root, record, "main", outcome)

    assert result.short_circuit is not None
    assert result.short_circuit.resolution_exhausted is False
    assert (
        result.short_circuit.last_reason is not None
        and "complete_in_place" in result.short_circuit.last_reason
    )


def test_next_strategy_outcome_does_not_raise_exhausted_failure(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat",
        pre_target_sha="base",
        resolving_rebase=True,
    )
    outcome = NextStrategyOutcome(strategy="complete_in_place")
    failure = _rebase_failure_transport(root, record, None, outcome, ())
    assert failure is not None
    assert not isinstance(failure, ExhaustedResolutionFailure)
    assert "complete_in_place" in failure


def test_candidate_exhausted_raises_exhausted_resolution_failure(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat",
        pre_target_sha="base",
        resolving_rebase=True,
    )
    outcome = RetryLaterOutcome(
        terminal_reason=ResolutionTerminationReason.CANDIDATE_EXHAUSTED,
        on_change="all agents failed",
    )
    failure = _rebase_failure_transport(root, record, None, outcome, ())
    assert isinstance(failure, ExhaustedResolutionFailure)
    assert failure.terminal_reason == ResolutionTerminationReason.CANDIDATE_EXHAUSTED


def test_integration_record_with_completion_advanced() -> None:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat",
        pre_target_sha="base",
        resolving_rebase=True,
        rebase_continue_pending=True,
        rebase_continue_head="h1",
        rebase_continue_stop="s1",
        rebase_continue_tree="t1",
        rebase_continue_error="err",
    )
    advanced = record.with_completion_advanced("integrated_sha_123")
    assert advanced.phase == "integrated"
    assert advanced.integrated_feature_sha == "integrated_sha_123"
    assert advanced.resolving_rebase is False
    assert advanced.rebase_continue_pending is False
    assert advanced.rebase_continue_head is None
    assert advanced.rebase_continue_stop is None
    assert advanced.rebase_continue_tree is None
    assert advanced.rebase_continue_error is None
