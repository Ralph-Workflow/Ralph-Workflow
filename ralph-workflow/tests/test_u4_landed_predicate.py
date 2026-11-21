from __future__ import annotations

import itertools
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.git.git_run_result import GitRunResult
from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.pipeline._auto_integrate_rebase_resolve import _rebase_run_result
from ralph.pipeline._integration_continuation import (
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
from ralph.process.manager import (
    ProcessManager,
    ProcessManagerPolicy,
    SpawnOptions,
    default_sync_process_factory,
    install_process_manager,
    reset_process_manager,
)
from ralph.testing.fake_process import FakeTimeoutPopen

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_next_strategy_outcome_returns_the_completion_strategy_text": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_next_strategy_outcome_does_not_raise_exhausted_failure with an unchanged body"
    ),
    "test_no_runnable_candidate_returns_a_typed_retry_later_outcome (renamed)": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; renamed from test_candidate_exhausted_raises_exhausted_resolution_failure with an unchanged body"
    ),
    "test_no_runnable_candidate_returns_a_typed_retry_later_outcome": (
        "operator rule: agent availability is cooldown-only and the cooldown system is its only name; the check is unchanged and now reads the renamed cooldown vocabulary (benches_agent -> starts_cooldown, verdict_benches_agent -> verdict_starts_cooldown, false_bench_* -> false_cooldown_*, CANDIDATE_EXHAUSTED -> NO_RUNNABLE_CANDIDATE, exhaustion_reason -> halt_reason, 'spent/unavailable' -> 'on cooldown')"
    ),
}


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


_ARGV_CAP = 256


def _seed_history_with_picked_target_commit(root: Path, target_commits: int) -> tuple[str, str]:
    stream: list[str] = [
        "commit refs/heads/main\nmark :1\ncommitter T <t@example.com> 1700000000 +0000\n"
        "data 4\nbase\nM 100644 inline base.txt\ndata 5\nbase\n\n"
    ]
    parent = 1
    for index in range(target_commits):
        mark = index + 2
        change = (
            "M 100644 inline picked.txt\ndata 7\npicked\n" if index == target_commits // 2 else ""
        )
        stream.append(
            f"commit refs/heads/main\nmark :{mark}\n"
            f"committer U <u@example.com> {1700000001 + index} +0000\n"
            f"data {len(f'target {index}')}\ntarget {index}\nfrom :{parent}\n{change}\n"
        )
        parent = mark
    stream.append("done\n")
    result = run_git(
        ("fast-import", "--quiet", "--done"),
        cwd=root,
        label="u4-landed-test",
        options=GitRunOptions(timeout=30, input_data="".join(stream)),
    )
    assert result.returncode == 0, result.stderr
    base = _value(root, "rev-list", "--max-parents=0", "main")
    assert _git(root, "reset", "--hard", "main").returncode == 0
    assert _git(root, "switch", "-c", "feature", base).returncode == 0
    _commit(root, "picked.txt", "picked\n", "feature picked")
    feature = _commit(root, "feature.txt", "feature\n", "feature work")
    return _value(root, "rev-parse", "main"), feature


def _argv_capped_git_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_git = shutil.which("git")
    assert real_git is not None
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    shim = shim_dir / "git"
    shim.write_text(
        f'#!/bin/sh\n[ "$#" -le {_ARGV_CAP} ] || {{ echo "argv of $# args" >&2; exit 97; }}\n'
        f'exec "{real_git}" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim_dir}:{Path(real_git).parent}")


def test_operation_landed_scales_to_a_target_with_thousands_of_commits_since_the_fork(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    target_tip, feature = _seed_history_with_picked_target_commit(root, 3000)
    assert _git(root, "rebase", "main").returncode == 0
    rebased_head = _value(root, "rev-parse", "HEAD")
    assert _value(root, "rev-list", "--count", f"{target_tip}..HEAD") == "1"
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feature,
        pre_target_sha=target_tip,
        resolving_rebase=True,
    )
    _argv_capped_git_on_path(tmp_path, monkeypatch)

    decision = operation_landed(root, record)

    assert decision.landed is True, decision.failures
    assert decision.proof == "rebased range equivalence"
    assert decision.observed_head == rebased_head


def test_operation_landed_ignores_equivalent_patches_older_than_the_fork(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    _commit(root, "shared.txt", "base\n", "base")
    _commit(root, "revived.txt", "revived\n", "add revived")
    assert _git(root, "rm", "-q", "revived.txt").returncode == 0
    assert _git(root, "commit", "-m", "drop revived").returncode == 0
    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "revived.txt", "revived\n", "revive on feature")
    assert _git(root, "switch", "main").returncode == 0
    target_tip = _commit(root, "target.txt", "target\n", "target work")
    assert _git(root, "switch", "feature").returncode == 0
    assert _git(root, "reset", "--hard", "main").returncode == 0
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feature,
        pre_target_sha=target_tip,
        resolving_rebase=True,
    )

    decision = operation_landed(root, record)

    assert decision.landed is False


def test_operation_landed_reports_not_landed_when_git_log_times_out(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    target_tip, feature = _seed_history_with_picked_target_commit(root, 3)
    assert _git(root, "rebase", "main").returncode == 0
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feature,
        pre_target_sha=target_tip,
        resolving_rebase=True,
    )
    pids = itertools.count(1)

    def log_times_out(command: Sequence[str], opts: SpawnOptions) -> object:
        if tuple(command[1:2]) == ("log",):
            return FakeTimeoutPopen(next(pids))
        return default_sync_process_factory(command, opts)

    reset_process_manager()
    install_process_manager(
        ProcessManager(
            policy=ProcessManagerPolicy(
                default_grace_period_s=0.0, kill_followup_timeout_s=0.0, log_events=False
            ),
            sync_process_factory=log_times_out,
        )
    )
    try:
        decision = operation_landed(root, record)
    finally:
        reset_process_manager()

    assert decision.landed is False
    assert decision.proof == "none"


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
    result = _rebase_run_result(root, record, "main", outcome)

    assert result.short_circuit is not None
    assert result.short_circuit.resolution_exhausted is False
    assert (
        result.short_circuit.last_reason is not None
        and "complete_in_place" in result.short_circuit.last_reason
    )


def test_next_strategy_outcome_returns_the_completion_strategy_text(tmp_path: Path) -> None:
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
    assert isinstance(failure, str)
    assert "complete_in_place" in failure


def test_no_runnable_candidate_returns_a_typed_retry_later_outcome(tmp_path: Path) -> None:
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
        terminal_reason=ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE,
        on_change="all agents failed",
    )
    failure = _rebase_failure_transport(root, record, None, outcome, ())
    assert isinstance(failure, RetryLaterOutcome)
    assert failure.terminal_reason == ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE
    assert "all agents failed" in (failure.on_change or "")


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
