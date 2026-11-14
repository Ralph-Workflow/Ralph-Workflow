from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.merge import MERGE_STATE_IN_PROGRESS, merge_state
from ralph.pipeline.auto_integrate import (
    auto_integrate_after_commit as _auto_integrate_after_commit,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.resolution_outcome import ResolvedOutcome, RetryLaterOutcome
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


_TEST_RESOLVER_ATTEMPTS = 2


def auto_integrate_after_commit(
    config: UnifiedConfig,
    workspace_scope: WorkspaceScope,
    state: RebaseState,
    **kwargs: object,
) -> RebaseState | None:
    kwargs.setdefault("sleep", lambda _seconds: None)
    kwargs.setdefault("jitter", lambda: 0.0)
    repo_root = Path(workspace_scope.root) if hasattr(workspace_scope, "root") else None
    if repo_root is not None:
        from ralph.pipeline.auto_integrate_record import read_record, write_record

        rec = read_record(repo_root)
        if rec is not None and rec.resolution_gate_key is not None:
            write_record(repo_root, rec.with_resolution_gate_key(None))
    return _auto_integrate_after_commit(config, workspace_scope, state, **kwargs)


def _run(repo_root: Path, *args: str, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _base_branch(tmp_git_repo: Path) -> str:
    from git import Repo

    return Repo(tmp_git_repo).active_branch.name


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    from git import Repo

    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repo = Repo(repo_root)
    try:
        repo.index.add([filename])
        return repo.index.commit(message).hexsha
    finally:
        repo.close()


def _build_config(target: str) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
            },
        }
    )


def _diverged_conflicting_repo(tmp_git_repo: Path) -> str:
    base = _base_branch(tmp_git_repo)
    base_seed_sha = _commit(tmp_git_repo, "base_seed.txt", "base seed\n", "base seed")
    _run(tmp_git_repo, "branch", "feature", base_seed_sha)
    _run(tmp_git_repo, "checkout", "feature")
    _commit(tmp_git_repo, "shared.txt", "feature version\n", "feature shared")
    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base version 1\n", "base shared 1")
    _run(tmp_git_repo, "checkout", "feature")
    return base


def _exhaust_budget(
    tmp_git_repo: Path, config: UnifiedConfig, resolver: ConflictResolver
) -> RebaseState:
    state = RebaseState()
    for _ in range(_TEST_RESOLVER_ATTEMPTS):
        result = auto_integrate_after_commit(
            config,
            WorkspaceScope(tmp_git_repo),
            state,
            conflict_resolver=resolver,
        )
        assert result is not None
        assert result.last_action == "conflict"
        state = result
    exhausted_state = state.model_copy(update={"resolution_exhausted": True})
    suppressed_result = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        exhausted_state,
        conflict_resolver=resolver,
    )
    assert suppressed_result is not None
    assert suppressed_result.last_action == "conflict"
    return suppressed_result


def _snapshot(tmp_git_repo: Path) -> tuple[str, str]:
    head = _run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip()
    status = _run(tmp_git_repo, "status", "--porcelain").stdout
    return head, status


def test_auto_integrate_regression_unresolvable_conflict_stops_reinvoking_the_resolver(
    tmp_git_repo: Path,
) -> None:
    from ralph.agents.timeout_clock import FakeClock
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact

    base = _diverged_conflicting_repo(tmp_git_repo)
    config = _build_config(base)
    invocations: list[str] = []

    def _never_resolves(repo_root: Path, target: str) -> RetryLaterOutcome:
        invocations.append(target)
        return RetryLaterOutcome(on_change="conflict state changes")

    state = RebaseState()
    outcomes: list[RebaseState] = []
    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=_TEST_RESOLVER_ATTEMPTS)
    candidates = ("resolver",)
    for _ in range(_TEST_RESOLVER_ATTEMPTS + 2):
        result = auto_integrate_after_commit(
            config,
            WorkspaceScope(tmp_git_repo),
            state,
            conflict_resolver=_never_resolves,
            ledger=ledger,
            candidates=candidates,
        )
        assert result is not None
        outcomes.append(result)
        state = result
        if result.last_action == "conflict":
            ledger.record(
                AttemptFact(
                    phase="rebase_conflict_resolution",
                    agent="resolver",
                    artifact_valid=False,
                )
            )

    assert len(invocations) == _TEST_RESOLVER_ATTEMPTS
    final = outcomes[-1]
    assert final.last_action == "conflict"
    assert not final.fast_forwarded
    assert final.recovery_record_retained
    assert not final.is_intervention_required
    assert not final.resolution_exhausted
    assert final.consecutive_conflicts == _TEST_RESOLVER_ATTEMPTS
    assert final.last_reason is not None
    assert "pending" in final.last_reason
    assert base in final.last_reason
    assert "budget exhausted" not in final.last_reason
    assert merge_state(tmp_git_repo) == MERGE_STATE_IN_PROGRESS


def test_configured_resolver_attempt_budget_overrides_compatibility_default(
    tmp_git_repo: Path,
) -> None:
    from ralph.agents.timeout_clock import FakeClock
    from ralph.pipeline.auto_integrate_record import read_record, write_record
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact

    base = _diverged_conflicting_repo(tmp_git_repo)
    config = _build_config(base)
    invocations: list[str] = []

    def _never_resolves(_root: Path, target: str) -> RetryLaterOutcome:
        invocations.append(target)
        return RetryLaterOutcome(on_change="conflict state changes")

    clock = FakeClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)
    candidates = ("resolver",)
    state = RebaseState()
    for _ in range(4):
        rec = read_record(tmp_git_repo)
        if rec is not None and rec.resolution_gate_key is not None:
            write_record(tmp_git_repo, rec.with_resolution_gate_key(None))
        result = auto_integrate_after_commit(
            config,
            WorkspaceScope(tmp_git_repo),
            state,
            conflict_resolver=_never_resolves,
            ledger=ledger,
            candidates=candidates,
        )
        assert result is not None
        state = result
        if result.last_action == "conflict":
            ledger.record(
                AttemptFact(
                    phase="rebase_conflict_resolution",
                    agent="resolver",
                    artifact_valid=False,
                )
            )

    assert invocations == [base] * 3


def test_first_conflict_attempt_is_never_suppressed(tmp_git_repo: Path) -> None:
    base = _diverged_conflicting_repo(tmp_git_repo)
    invocations: list[str] = []

    def _never_resolves(repo_root: Path, target: str) -> RetryLaterOutcome:
        invocations.append(target)
        return RetryLaterOutcome(on_change="conflict state changes")

    result = auto_integrate_after_commit(
        _build_config(base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(),
        conflict_resolver=_never_resolves,
    )

    assert invocations == [base]
    assert result is not None
    assert result.consecutive_conflicts == 1


def test_successful_land_resets_the_conflict_budget(tmp_git_repo: Path) -> None:
    base = _diverged_conflicting_repo(tmp_git_repo)

    def _resolves(repo_root: Path, target: str) -> ResolvedOutcome:
        (repo_root / "shared.txt").write_text("feature version\nbase version 1\n", encoding="utf-8")
        return ResolvedOutcome()

    result = auto_integrate_after_commit(
        _build_config(base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(
            last_action="conflict",
            last_target=base,
            consecutive_conflicts=_TEST_RESOLVER_ATTEMPTS - 1,
        ),
        conflict_resolver=_resolves,
    )

    assert result is not None
    assert result.fast_forwarded is True
    assert result.consecutive_conflicts == 0


def test_exhausted_budget_still_aborts_the_endpoint_merge_bit_identically(
    tmp_git_repo: Path,
) -> None:
    base = _diverged_conflicting_repo(tmp_git_repo)
    invocations: list[str] = []

    def _never_resolves(repo_root: Path, target: str) -> RetryLaterOutcome:
        invocations.append(target)
        return RetryLaterOutcome(on_change="conflict state changes")

    before = _snapshot(tmp_git_repo)

    result = auto_integrate_after_commit(
        _build_config(base),
        WorkspaceScope(tmp_git_repo),
        RebaseState(
            last_action="conflict",
            last_target=base,
            consecutive_conflicts=_TEST_RESOLVER_ATTEMPTS,
            resolution_exhausted=True,
        ),
        conflict_resolver=_never_resolves,
    )

    assert invocations == []
    assert result is not None
    assert result.last_action == "conflict"
    assert _snapshot(tmp_git_repo) == before
    assert not (tmp_git_repo / ".git" / "MERGE_HEAD").exists()


def test_new_feature_commit_gets_a_fresh_resolver_budget(
    tmp_git_repo: Path,
) -> None:
    base = _diverged_conflicting_repo(tmp_git_repo)
    config = _build_config(base)
    invocations: list[str] = []

    def _never_resolves(repo_root: Path, target: str) -> RetryLaterOutcome:
        invocations.append(target)
        return RetryLaterOutcome(on_change="conflict state changes")

    spent = _exhaust_budget(tmp_git_repo, config, _never_resolves)
    assert len(invocations) == _TEST_RESOLVER_ATTEMPTS
    assert spent.last_conflict_feature_sha is not None
    assert spent.last_conflict_target_sha is not None

    new_feature_sha = _commit(tmp_git_repo, "shared.txt", "feature version 2\n", "feature shared 2")

    result = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        spent,
        conflict_resolver=_never_resolves,
    )

    assert invocations == [base] * (_TEST_RESOLVER_ATTEMPTS + 1)
    assert result is not None
    assert result.last_action == "conflict"
    assert result.consecutive_conflicts == 1
    assert result.last_conflict_feature_sha == new_feature_sha
    assert result.last_reason is not None
    assert "budget" not in result.last_reason


def test_moved_mainline_tip_gets_a_fresh_resolver_budget(
    tmp_git_repo: Path,
) -> None:
    base = _diverged_conflicting_repo(tmp_git_repo)
    config = _build_config(base)
    invocations: list[str] = []

    def _never_resolves(repo_root: Path, target: str) -> RetryLaterOutcome:
        invocations.append(target)
        return RetryLaterOutcome(on_change="conflict state changes")

    spent = _exhaust_budget(tmp_git_repo, config, _never_resolves)
    assert len(invocations) == _TEST_RESOLVER_ATTEMPTS

    _run(tmp_git_repo, "checkout", base)
    _commit(tmp_git_repo, "shared.txt", "base version 2\n", "base shared 2")
    moved_target_sha = _run(tmp_git_repo, "rev-parse", f"refs/heads/{base}").stdout.strip()
    _run(tmp_git_repo, "checkout", "feature")

    result = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        spent,
        conflict_resolver=_never_resolves,
    )

    assert invocations == [base] * (_TEST_RESOLVER_ATTEMPTS + 1)
    assert result is not None
    assert result.last_action == "conflict"
    assert result.consecutive_conflicts == 1
    assert result.last_conflict_target_sha == moved_target_sha


def test_unchanged_conflict_after_a_skip_stays_suppressed(
    tmp_git_repo: Path,
) -> None:
    base = _diverged_conflicting_repo(tmp_git_repo)
    config = _build_config(base)
    invocations: list[str] = []

    def _never_resolves(repo_root: Path, target: str) -> RetryLaterOutcome:
        invocations.append(target)
        return RetryLaterOutcome(on_change="conflict state changes")

    spent = _exhaust_budget(tmp_git_repo, config, _never_resolves)
    assert len(invocations) == _TEST_RESOLVER_ATTEMPTS

    (tmp_git_repo / "base_seed.txt").write_text("dirty\n", encoding="utf-8")
    skipped = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        spent,
        conflict_resolver=_never_resolves,
    )
    assert skipped is not None
    assert skipped.last_action == "skipped"
    assert skipped.consecutive_conflicts == spent.consecutive_conflicts
    assert skipped.last_conflict_feature_sha == spent.last_conflict_feature_sha
    assert skipped.last_conflict_target_sha == spent.last_conflict_target_sha

    (tmp_git_repo / "base_seed.txt").write_text("base seed\n", encoding="utf-8")
    result = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        skipped,
        conflict_resolver=_never_resolves,
    )

    assert invocations == [base] * _TEST_RESOLVER_ATTEMPTS
    assert result is not None
    assert result.last_reason is not None
    assert "budget" in result.last_reason


def test_auto_integrate_regression_resolver_invocation_failure_preserves_rebase_budget(
    tmp_git_repo: Path,
) -> None:
    from ralph.pipeline.resolution_outcome import UnknownOutcome

    base = _diverged_conflicting_repo(tmp_git_repo)
    config = _build_config(base)
    prior = RebaseState(
        last_action="conflict",
        last_target=base,
        consecutive_conflicts=1,
        last_conflict_feature_sha=_run(tmp_git_repo, "rev-parse", "HEAD").stdout.strip(),
        last_conflict_target_sha=_run(tmp_git_repo, "rev-parse", base).stdout.strip(),
    )
    calls: list[str] = []

    def _resolver(_root: Path, target: str) -> UnknownOutcome:
        calls.append(target)
        return UnknownOutcome(RuntimeError("resolver invocation failed"))

    result = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        prior,
        conflict_resolver=_resolver,
    )

    assert calls == [base]
    assert result is not None
    assert result.last_action == "conflict"
    assert result.consecutive_conflicts == prior.consecutive_conflicts
    assert result.conflict_strategy_index == prior.conflict_strategy_index


def test_unexpected_failure_mid_attempt_does_not_refund_the_budget(
    tmp_git_repo: Path,
) -> None:
    base = _diverged_conflicting_repo(tmp_git_repo)
    config = _build_config(base)

    def _never_resolves(repo_root: Path, target: str) -> RetryLaterOutcome:
        return RetryLaterOutcome(on_change="conflict state changes")

    spent = _exhaust_budget(tmp_git_repo, config, _never_resolves)

    retained = auto_integrate_after_commit(
        config,
        WorkspaceScope(tmp_git_repo),
        spent,
        conflict_resolver=_never_resolves,
    )

    assert retained is not None
    assert retained.last_action == "conflict"
    assert retained.consecutive_conflicts >= spent.consecutive_conflicts
    assert retained.last_conflict_feature_sha == spent.last_conflict_feature_sha
