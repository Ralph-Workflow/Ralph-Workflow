from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.pipeline import IntegrationAction
from ralph.pipeline._integration_action import RebaseOutcomeKind, next_integration_action
from ralph.pipeline._integration_diagnostic import (
    DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
    diagnose_retained_integration,
)
from ralph.pipeline._integration_record_facts import (
    apply_record_facts,
    record_requires_intervention,
)
from ralph.pipeline._terminal_authority import is_terminal_success_eligible
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
from ralph.pipeline.integration_decision import CleanupLadderRung
from ralph.pipeline.integration_owner import (
    RecordFactOutcome,
    ladder_rung_for_record_fact_outcome,
)
from ralph.pipeline.integration_resolution import (
    IntegrationResolutionStatus,
    inspect_integration_resolution,
)
from ralph.pipeline.operation_evidence_memory import InMemoryOperationEvidenceCollector
from ralph.pipeline.operation_fact import OperationFact
from ralph.pipeline.rebase_state import RebaseState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.git.git_run_result import GitRunResult
    from ralph.pipeline.conflict_resolution import RebaseStop

pytestmark = [pytest.mark.timeout_seconds(10)]


def _git(root: Path, *args: str) -> GitRunResult:
    return run_git(
        args,
        cwd=root,
        label="retained-integration-acceptance",
        options=GitRunOptions(timeout=5),
    )


def _commit(root: Path, path: str, content: str) -> str:
    (root / path).write_text(content, encoding="utf-8")
    from git import Repo

    repo = Repo(root)
    try:
        repo.index.add([path])
        commit = repo.index.commit(path)
        assert commit.hexsha
        return commit.hexsha
    finally:
        repo.close()


def test_acceptance_scenario_1_linked_worktree_unrelated_branch_advance(
    tmp_git_repo: Path,
    tmp_path: Path,
) -> None:
    root = tmp_git_repo
    target = _git(root, "branch", "--show-current").stdout.strip()
    base = _commit(root, "shared.txt", "base\n")

    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "feature.txt", "feature content\n")

    initial_record = IntegrationRecord(
        phase="integrated",
        target=target,
        pre_feature_sha=feature,
        pre_target_sha=base,
        integrated_feature_sha=feature,
        reintegrate_pending=True,
    )
    write_record(root, initial_record)
    assert initial_record.phase == "integrated"
    assert initial_record.reintegrate_pending is True
    assert initial_record.merge_commit_repair_attempts == 0
    assert initial_record.diagnostic_intervention_required is False

    other_worktree = tmp_path / "other-worktree"
    assert (
        _git(root, "worktree", "add", "-b", "unrelated-branch", str(other_worktree)).returncode == 0
    )
    unrelated_ref_1 = _commit(other_worktree, "unrelated.txt", "unrelated commit 1\n")
    unrelated_ref_2 = _commit(other_worktree, "unrelated.txt", "unrelated commit 2\n")
    assert unrelated_ref_1 != unrelated_ref_2
    assert _git(root, "rev-parse", "unrelated-branch").stdout.strip() == unrelated_ref_2

    action = next_integration_action(initial_record)
    assert action == IntegrationAction.REINTEGRATE_AGAINST_TARGET
    rebase_state = RebaseState()
    initial_resolution = inspect_integration_resolution(root, rebase_state)
    assert initial_resolution.dispatch_allowed is False
    assert initial_resolution.status.name == "RECOVERABLE"
    assert is_terminal_success_eligible(None, initial_record, rebase_state) is False

    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_target": target,
                "auto_integrate_remote_enabled": False,
            }
        }
    )

    outcome = recover_incomplete_integration(WorkspaceScope(root), config=config)
    assert outcome is not None
    assert outcome.fast_forwarded is True
    assert outcome.recovery_record_retained is False
    assert (
        _git(root, "rev-parse", target).stdout.strip()
        == _git(root, "rev-parse", feature).stdout.strip()
    )
    assert read_record(root) is None

    final_resolution = inspect_integration_resolution(root, outcome)
    assert final_resolution.dispatch_allowed is True
    assert final_resolution.status.name == "RESOLVED"


def test_acceptance_scenario_2_target_moved_during_integration(
    tmp_git_repo: Path,
) -> None:
    root = tmp_git_repo
    target = _git(root, "branch", "--show-current").stdout.strip()
    base = _commit(root, "shared.txt", "base\n")

    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "shared.txt", "feature\n")

    write_record(
        root,
        IntegrationRecord(
            phase="integrated",
            target=target,
            pre_feature_sha=feature,
            pre_target_sha=base,
            integrated_feature_sha=feature,
        ),
    )

    assert _git(root, "switch", target).returncode == 0
    _commit(root, "shared.txt", "target moved\n")
    assert _git(root, "switch", "feature").returncode == 0

    record = read_record(root)
    assert record is not None

    action = next_integration_action(record.model_copy(update={"target_moved": True}))
    assert action == IntegrationAction.REINTEGRATE_AGAINST_TARGET

    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_target": target,
                "auto_integrate_remote_enabled": False,
            }
        }
    )

    declined = recover_incomplete_integration(
        WorkspaceScope(root),
        config=config,
        rebase_stop_resolver=lambda _root, _target, _stop: False,
        conflict_resolver=lambda _root, _target: False,
    )
    assert declined is not None and not declined.fast_forwarded
    assert read_record(root) is not None
    assert not inspect_integration_resolution(root, RebaseState()).dispatch_allowed

    def resolve(repo: Path, _branch: str, _stop: RebaseStop) -> bool:
        (repo / "shared.txt").write_text("feature and target\n", encoding="utf-8")
        return True

    outcome = recover_incomplete_integration(
        WorkspaceScope(root),
        config=config,
        rebase_stop_resolver=resolve,
    )
    assert outcome is not None and outcome.fast_forwarded is True
    assert read_record(root) is None


def test_acceptance_scenario_5_conflict_resolved_landing_pending(
    tmp_git_repo: Path,
) -> None:
    root = tmp_git_repo
    target = _git(root, "branch", "--show-current").stdout.strip()
    base = _commit(root, "shared.txt", "base\n")

    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "feature.txt", "feature content\n")

    record = IntegrationRecord(
        phase="integrated",
        target=target,
        pre_feature_sha=feature,
        pre_target_sha=base,
        integrated_feature_sha=feature,
        landing_pending=True,
    )
    write_record(root, record)

    action = next_integration_action(record)
    assert action == IntegrationAction.LAND_TARGET

    rebase_state = RebaseState(
        last_outcome=RebaseOutcomeKind.MERGED,
        fast_forwarded=False,
    )
    resolution = inspect_integration_resolution(root, rebase_state)
    assert resolution.dispatch_allowed is False
    assert resolution.status.name == "RECOVERABLE"

    assert is_terminal_success_eligible(None, record, rebase_state) is False

    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_target": target,
                "auto_integrate_remote_enabled": False,
            }
        }
    )
    outcome = recover_incomplete_integration(WorkspaceScope(root), config=config)
    assert outcome is not None and outcome.fast_forwarded is True
    assert read_record(root) is None
    assert (
        _git(root, "rev-parse", target).stdout.strip()
        == _git(root, "rev-parse", feature).stdout.strip()
    )


def test_acceptance_scenario_4_public_boundary_bounded_recovery_to_ladder_escalation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        resolving_rebase=True,
        merge_commit_repair_attempts=0,
        owning_worktree=str(root),
    )
    write_record(root, record)

    bundle = load_policy(Path(__file__).parents[2] / "ralph" / "policy" / "defaults")
    ctx = MagicMock()
    ctx.workspace_scope = WorkspaceScope(root)
    ctx.config = UnifiedConfig()
    ctx.pipeline_deps = MagicMock()
    ctx.policy_bundle = bundle
    ctx.registry: dict[str, object] = {}
    ctx.active_display = MagicMock()
    ctx.display_context = None

    collector = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="rebase_object_resolvable", value="unreadable"),),
    )

    for expected_attempts in range(1, DIAGNOSTIC_REPAIR_ATTEMPT_CAP + 1):
        assert not diagnose_retained_integration(ctx, "unreadable evidence", collector=collector), (
            f"call {expected_attempts} should return False"
        )
        saved = read_record(root)
        assert saved is not None
        assert saved.merge_commit_repair_attempts == min(
            expected_attempts, DIAGNOSTIC_REPAIR_ATTEMPT_CAP
        ), (
            f"attempt counter should be {min(expected_attempts, DIAGNOSTIC_REPAIR_ATTEMPT_CAP)}, "
            f"got {saved.merge_commit_repair_attempts}"
        )
        assert saved.diagnostic_ownership is not None, (
            f"call {expected_attempts}: diagnostic_ownership (lease) should be set"
        )
        if record_requires_intervention(saved):
            overlay = apply_record_facts(IntegrationAction.RESOLVE_ACTIVE_OPERATION, saved)
            assert overlay is RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT
            assert ladder_rung_for_record_fact_outcome(overlay) is CleanupLadderRung.NORMAL_CLEANUP

    after_restart = read_record(root)
    assert after_restart is not None
    assert after_restart.merge_commit_repair_attempts == DIAGNOSTIC_REPAIR_ATTEMPT_CAP
    restart_overlay = apply_record_facts(IntegrationAction.RESOLVE_ACTIVE_OPERATION, after_restart)
    assert restart_overlay is not IntegrationAction.REQUIRE_INTERVENTION
    if isinstance(restart_overlay, RecordFactOutcome):
        assert (
            ladder_rung_for_record_fact_outcome(restart_overlay) is CleanupLadderRung.NORMAL_CLEANUP
        )

    assert not diagnose_retained_integration(ctx, "unreadable evidence", collector=collector)
    after_extra = read_record(root)
    assert after_extra is not None
    assert after_extra.merge_commit_repair_attempts == DIAGNOSTIC_REPAIR_ATTEMPT_CAP, (
        "counter must not grow after exhaustion"
    )

    resolution = inspect_integration_resolution(root, RebaseState())
    assert resolution.dispatch_allowed is False
    assert resolution.status == IntegrationResolutionStatus.RECOVERABLE
    final_overlay = apply_record_facts(IntegrationAction.RESOLVE_ACTIVE_OPERATION, after_restart)
    assert final_overlay is not IntegrationAction.REQUIRE_INTERVENTION
    if isinstance(final_overlay, RecordFactOutcome):
        assert (
            ladder_rung_for_record_fact_outcome(final_overlay) is CleanupLadderRung.NORMAL_CLEANUP
        )
    assert is_terminal_success_eligible(None, after_restart, RebaseState()) is False

    assert not diagnose_retained_integration(
        ctx, "unreadable evidence after restart", collector=collector
    )
    final = read_record(root)
    assert final is not None
    assert final.merge_commit_repair_attempts == DIAGNOSTIC_REPAIR_ATTEMPT_CAP
