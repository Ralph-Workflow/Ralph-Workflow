from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.pipeline._integration_action import (
    IntegrationAction,
    RebaseOutcomeKind,
    next_integration_action,
)
from ralph.pipeline._integration_diagnostic import (
    DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
    diagnose_retained_integration,
)
from ralph.pipeline._terminal_authority import is_terminal_success_eligible
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
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

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


def _git(root: Path, *args: str) -> GitRunResult:
    return run_git(
        args,
        cwd=root,
        label="retained-integration-acceptance",
        options=GitRunOptions(timeout=5),
    )


def _commit(root: Path, path: str, content: str) -> str:
    (root / path).write_text(content, encoding="utf-8")
    assert _git(root, "add", path).returncode == 0
    assert _git(root, "commit", "-m", path).returncode == 0
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def test_acceptance_scenario_1_linked_worktree_unrelated_branch_advance(
    tmp_git_repo: Path,
    tmp_path: Path,
) -> None:
    """Scenario 1: Advance unrelated branch in linked worktree while retained obligation exists.

    In linked worktrees, advance an unrelated branch while another worktree has
    a retained integration obligation. The obligation must remain valid and make
    progress according to its own state.
    """
    root = tmp_git_repo
    target = _git(root, "branch", "--show-current").stdout.strip()
    base = _commit(root, "shared.txt", "base\n")

    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "feature.txt", "feature content\n")

    # Retained integration obligation in root worktree with pending reintegration
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

    # Create linked worktree with unrelated branch and induce ref movement
    other_worktree = tmp_path / "other-worktree"
    assert (
        _git(root, "worktree", "add", "-b", "unrelated-branch", str(other_worktree)).returncode == 0
    )
    unrelated_ref_1 = _commit(other_worktree, "unrelated.txt", "unrelated commit 1\n")
    unrelated_ref_2 = _commit(other_worktree, "unrelated.txt", "unrelated commit 2\n")
    assert unrelated_ref_1 != unrelated_ref_2
    assert _git(root, "rev-parse", "unrelated-branch").stdout.strip() == unrelated_ref_2

    # Observable lifecycle progress before recovery: dispatch blocked, reintegration required
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

    # In root worktree, recover integration; must progress and land despite unrelated branch advances
    outcome = recover_incomplete_integration(WorkspaceScope(root), config=config)
    assert outcome is not None
    assert outcome.fast_forwarded is True
    assert outcome.recovery_record_retained is False
    assert (
        _git(root, "rev-parse", target).stdout.strip()
        == _git(root, "rev-parse", feature).stdout.strip()
    )
    assert read_record(root) is None

    # Observable lifecycle progress after recovery: dispatch allowed, operation resolved
    final_resolution = inspect_integration_resolution(root, outcome)
    assert final_resolution.dispatch_allowed is True
    assert final_resolution.status.name == "RESOLVED"


def test_acceptance_scenario_2_target_moved_during_integration(
    tmp_git_repo: Path,
) -> None:
    """Scenario 2: Move configured target during integration.

    Ownership must remain and the next action must become reintegration
    against the new target.
    """
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

    # Target moves while integration record exists
    assert _git(root, "switch", target).returncode == 0
    _commit(root, "shared.txt", "target moved\n")
    assert _git(root, "switch", "feature").returncode == 0

    record = read_record(root)
    assert record is not None

    # Next action must be reintegrating against target, not landing or completion
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

    # Reintegrating against target keeps ownership and blocks dispatch until resolved
    declined = recover_incomplete_integration(
        WorkspaceScope(root),
        config=config,
        rebase_stop_resolver=lambda _root, _target, _stop: False,
        conflict_resolver=lambda _root, _target: False,
    )
    assert declined is not None and not declined.fast_forwarded
    assert read_record(root) is not None
    assert not inspect_integration_resolution(root, RebaseState()).dispatch_allowed

    # Resolve conflict to finish reintegration and landing
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
    """Scenario 5: Conflict resolution completed, target landing pending.

    Ralph must continue the integration lifecycle rather than re-enter development.
    """
    root = tmp_git_repo
    target = _git(root, "branch", "--show-current").stdout.strip()
    base = _commit(root, "shared.txt", "base\n")

    assert _git(root, "switch", "-c", "feature").returncode == 0
    feature = _commit(root, "feature.txt", "feature content\n")

    # Conflict was resolved into a rebased/merged feature, but target hasn't landed
    record = IntegrationRecord(
        phase="integrated",
        target=target,
        pre_feature_sha=feature,
        pre_target_sha=base,
        integrated_feature_sha=feature,
        landing_pending=True,
    )
    write_record(root, record)

    # Next action must be LAND_TARGET
    action = next_integration_action(record)
    assert action == IntegrationAction.LAND_TARGET

    # Resolution inspection must show dispatch is blocked and recoverable
    rebase_state = RebaseState(
        last_outcome=RebaseOutcomeKind.MERGED,
        fast_forwarded=False,
    )
    resolution = inspect_integration_resolution(root, rebase_state)
    assert resolution.dispatch_allowed is False
    assert resolution.status.name == "RECOVERABLE"

    # Terminal authority must forbid terminal success while landing is pending
    assert is_terminal_success_eligible(None, record, rebase_state) is False

    # Integration lifecycle continues to land target
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


def test_acceptance_scenario_4_public_boundary_bounded_recovery_to_intervention(
    tmp_path: Path,
) -> None:
    """Scenario 4 (WU-S4): UNKNOWN evidence durable increment to intervention.

    ``diagnose_retained_integration`` is driven end-to-end through its
    public signature with an injected collector whose evidence returns
    ``EvidenceVerdict.UNKNOWN`` on every call (value = "unreadable" sentinel).
    Each call durably increments ``merge_commit_repair_attempts`` by exactly 1
    (no agents registered → ``handoff_integration_diagnostic`` bumps the
    counter instead of dispatching).  After exactly
    ``DIAGNOSTIC_REPAIR_ATTEMPT_CAP`` calls the record carries
    ``diagnostic_intervention_required=True``.  A simulated restart (reload
    from disk) confirms the exhausted state persists and a further call is a
    no-op — no extra attempt, no counter reset.
    """
    root = tmp_path / "repo"
    root.mkdir()

    # Start clean: zero attempts so every call drives the UNKNOWN bump path.
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
    ctx.registry: dict[str, object] = {}  # no agents → bumps counter
    ctx.active_display = MagicMock()
    ctx.display_context = None

    # Collector returns "unreadable" sentinel on every snapshot.
    # This makes ``evaluate()`` → ``EvidenceVerdict.UNKNOWN`` and also
    # makes ``diagnostic_evidence_verdict`` → ``UNKNOWN`` on subsequent
    # calls (the stored evidence also carries value="unreadable").
    # "unreadable" does NOT match the ("False", "false") pre_pass guard,
    # so we never take the CHANGED/immediate-intervention shortcut.
    collector = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="rebase_object_resolvable", value="unreadable"),),
    )

    # (a) dispatch_allowed is False throughout.
    # (b) Each call increments merge_commit_repair_attempts by exactly 1.
    # (c) Durability: reload from disk after every write and confirm counter.
    for expected_attempts in range(1, DIAGNOSTIC_REPAIR_ATTEMPT_CAP + 1):
        assert not diagnose_retained_integration(ctx, "unreadable evidence", collector=collector), (
            f"call {expected_attempts} should return False"
        )
        saved = read_record(root)
        assert saved is not None
        assert saved.merge_commit_repair_attempts == expected_attempts, (
            f"attempt counter should be {expected_attempts}, got "
            f"{saved.merge_commit_repair_attempts}"
        )
        assert saved.diagnostic_intervention_required is (
            expected_attempts >= DIAGNOSTIC_REPAIR_ATTEMPT_CAP
        ), (
            f"intervention should be {expected_attempts >= DIAGNOSTIC_REPAIR_ATTEMPT_CAP}, got {saved.diagnostic_intervention_required}"
        )
        assert saved.diagnostic_evidence not in (None, ()), (
            f"call {expected_attempts}: diagnostic_evidence should be set"
        )

    # (c) Simulated restart: reload from disk and confirm exhausted state.
    after_restart = read_record(root)
    assert after_restart is not None
    assert after_restart.merge_commit_repair_attempts == DIAGNOSTIC_REPAIR_ATTEMPT_CAP
    assert after_restart.diagnostic_intervention_required is True

    # (d) A further call after restart does NOT enter the diagnostic repair path
    # again — no extra attempt, no counter reset.
    assert not diagnose_retained_integration(ctx, "unreadable evidence", collector=collector)
    after_extra = read_record(root)
    assert after_extra is not None
    assert after_extra.merge_commit_repair_attempts == DIAGNOSTIC_REPAIR_ATTEMPT_CAP, (
        "counter must not grow after exhaustion"
    )

    # (a) dispatch_allowed stays False; (e) terminal success remains blocked.
    resolution = inspect_integration_resolution(root, RebaseState())
    assert resolution.dispatch_allowed is False
    assert resolution.status == IntegrationResolutionStatus.RECOVERABLE
    assert next_integration_action(after_restart) == IntegrationAction.REQUIRE_INTERVENTION
    assert is_terminal_success_eligible(None, after_restart, RebaseState()) is False

    # One more no-op call to round out the proof.
    assert not diagnose_retained_integration(
        ctx, "unreadable evidence after restart", collector=collector
    )
    final = read_record(root)
    assert final is not None
    assert final.merge_commit_repair_attempts == DIAGNOSTIC_REPAIR_ATTEMPT_CAP
