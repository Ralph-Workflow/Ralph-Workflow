"""Fake-based lifecycle acceptance tests (Unit U8b).

Covers acceptance scenarios 3, 4, 6, 7, 8, 9 from PRODUCT_CRITERIA.md:
3. Interrupt each major lifecycle transition and restart. The same authority
   must continue idempotently without replaying completed business work.
4. Make required evidence unreadable. Dispatch and success remain blocked, and
   bounded recovery ends in visible intervention rather than an infinite loop.
6. Complete local landing with remote publication unavailable. The result must
   reflect the configured publication policy without confusing local and remote truth.
7. Exercise startup, phase-boundary, commit, cooldown, parallel join, and
   terminal entry points against the same underlying state. Every entry point
   must produce the same lifecycle decision.
8. Load legacy checkpoint and integration representations. Migration must
   produce one authoritative state or explicit intervention, never merge
   contradictory truths heuristically.
9. Exhaust recovery, restart Ralph, and confirm no automatic attempt budget is
   silently replenished.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock

from ralph.agents.timeout_clock import FakeClock
from ralph.checkpoint.checkpoint_payload import CheckpointPayload
from ralph.checkpoint.execution_history import ExecutionHistory
from ralph.checkpoint.execution_step import ExecutionStep
from ralph.checkpoint.run_context import RunContext
from ralph.checkpoint.step_outcome import StepOutcome
from ralph.config.models import UnifiedConfig
from ralph.git.remote_push import PushResult, PushStatus
from ralph.pipeline._integration_action import (
    IntegrationAction,
    RebaseOutcomeKind,
    next_integration_action,
)
from ralph.pipeline._retry_progress_guard import RetryProgressGuard
from ralph.pipeline.auto_integrate_record import (
    MARK_RESOLVING_REBASE,
    PROMOTE_TO_INTEGRATED,
    START_INTEGRATING,
    IntegrationRecord,
    IntegrationRecordStore,
    parse_record_payload,
    read_record,
)
from ralph.pipeline.auto_integrate_remote_sync import (
    REMOTE_REMOTE_UNREACHABLE,
    push_target_after_landing,
)
from ralph.pipeline.integration_resolution import inspect_integration_resolution
from ralph.pipeline.integration_resolution_status import IntegrationResolutionStatus
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from tests.test_checkpoint_and_record_persistence_boundary import _InMemoryBackend


@dataclass
class _FakeRemoteClient:
    """Injected fake remote client for testing remote publication boundaries."""

    push_status: PushStatus = PushStatus.UNREACHABLE
    push_detail: str = "remote unreachable"
    push_calls: list[tuple[Path, str, str, float]] = field(default_factory=list)

    def push_branch(
        self,
        repo_root: Path,
        branch: str,
        *,
        remote: str,
        timeout_seconds: float,
    ) -> PushResult:
        self.push_calls.append((repo_root, branch, remote, timeout_seconds))
        return PushResult(
            status=self.push_status,
            remote=remote,
            branch=branch,
            detail=self.push_detail,
        )

    def refresh_target(
        self,
        repo_root: Path,
        target: str,
        *,
        timeout_seconds: float,
        remote: str,
    ) -> str:
        del repo_root, target, timeout_seconds, remote
        return "unreachable"

    def observe_remote_sha(
        self,
        repo_root: Path,
        target: str,
        *,
        remote: str,
    ) -> str | None:
        del repo_root, target, remote
        return None


def test_acceptance_scenario_3_transition_interrupt_and_restart() -> None:
    """Scenario 3: Interrupt transitions and restart idempotently.

    The same authority must continue idempotently without replaying
    completed business work.
    """
    backend = _InMemoryBackend()
    root = Path("/workspace-crash-test")
    store = IntegrationRecordStore(root, backend=backend)

    # Transition 1: Start integrating
    step1 = store.transition(
        START_INTEGRATING,
        target="main",
        pre_feature_sha="1" * 40,
        pre_target_sha="2" * 40,
    )
    assert step1.phase == "integrating"
    assert read_record(root, backend=backend) == step1

    # Simulate crash before step 2: new store instance reading existing backend
    store_restart_1 = IntegrationRecordStore(root, backend=backend)
    step2 = store_restart_1.transition(MARK_RESOLVING_REBASE, resolving_rebase=True)
    assert step2.resolving_rebase is True
    assert read_record(root, backend=backend) == step2

    # Simulate crash after step 2: restart and replay step 2 idempotently
    store_restart_2 = IntegrationRecordStore(root, backend=backend)
    step2_replayed = store_restart_2.transition(MARK_RESOLVING_REBASE, resolving_rebase=True)
    assert step2_replayed == step2

    # Transition 3: Promote to integrated
    step3 = store_restart_2.transition(
        PROMOTE_TO_INTEGRATED,
        phase="integrated",
        integrated_feature_sha="3" * 40,
    )
    assert step3.phase == "integrated"
    assert step3.is_integrated is True
    assert read_record(root, backend=backend) == step3


def test_acceptance_scenario_4_unreadable_evidence_fails_closed() -> None:
    """Scenario 4: Make required evidence unreadable.

    Dispatch and success remain blocked, and bounded recovery ends in
    visible intervention rather than an infinite loop.
    """
    # Malformed dictionaries fail closed during record parsing
    assert parse_record_payload({"phase": "invalid_phase"}) is None
    assert parse_record_payload({"phase": 123}) is None

    # Unreadable Git evidence produces blocking verdict in resolution inspection
    verdict = inspect_integration_resolution(
        Path("/nonexistent"),
        RebaseState(),
        porcelain=lambda _p: (False, "git query failure"),
    )
    assert verdict.dispatch_allowed is False
    assert verdict.status == IntegrationResolutionStatus.RECOVERABLE

    # Action authority maps record requiring intervention to REQUIRE_INTERVENTION
    corrupted_record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat",
        pre_target_sha="main",
        diagnostic_intervention_required=True,
    )
    action = next_integration_action(corrupted_record)
    assert action == IntegrationAction.REQUIRE_INTERVENTION

    # Bounded retry: after deadline expires, action is REQUIRE_INTERVENTION, not infinite retry
    expired_state = RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        retry_deadline=100.0,
    )
    clock = FakeClock(start=150.0)  # Past deadline
    action_expired = next_integration_action(expired_state, clock=clock.monotonic)
    assert action_expired == IntegrationAction.REQUIRE_INTERVENTION


def test_acceptance_scenario_6_local_landing_complete_remote_unavailable() -> None:
    """Scenario 6: Complete local landing with remote publication unavailable.

    The result must reflect the configured publication policy without
    confusing local and remote truth.
    """
    fake_remote = _FakeRemoteClient(
        push_status=PushStatus.UNREACHABLE,
        push_detail="connection refused to origin",
    )
    repo_root = Path("/fake/repo")
    config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_target": "main",
                "auto_integrate_remote_enabled": True,
                "auto_integrate_remote": "origin",
            }
        }
    )

    # Local landing succeeded (fast_forwarded=True, freshness_verdict verified)
    rebase_state = RebaseState(
        last_outcome=RebaseOutcomeKind.FAST_FORWARDED,
        fast_forwarded=True,
        freshness_verdict="verified",
        last_action="rebased",
    )

    # Attempt remote sync with unavailable remote
    result_state = push_target_after_landing(
        config,
        repo_root,
        "main",
        rebase_state,
        remote_client=fake_remote,
    )
    assert result_state.last_remote_sync == REMOTE_REMOTE_UNREACHABLE
    assert result_state.last_publication_decision == "publish_deferred_remote"
    assert len(fake_remote.push_calls) == 1

    # Local truth: landing completed
    assert result_state.fast_forwarded is True

    # Remote truth: publication did not complete cleanly
    assert result_state.last_outcome != RebaseOutcomeKind.REMOTE_PUSHED

    # If publication policy requires publication, action requires publication
    policy_with_pub = MagicMock()
    policy_with_pub.publication_policy = "always"
    action = next_integration_action(result_state, policy=policy_with_pub)
    assert action == IntegrationAction.PUBLISH_AFTER_LANDING


def test_acceptance_scenario_7_entry_point_parity(tmp_path: Path) -> None:
    """Scenario 7: Parity across all 6 entry points.

    Exercise startup, phase-boundary, commit, cooldown, parallel join, and
    terminal entry points against the same underlying state. Every entry point
    must produce the same lifecycle decision.
    """
    from tests.test_public_workflow_entry_point_matrix import (
        test_entry_point_matrix_evaluates_consistent_phase_decision,
    )

    test_entry_point_matrix_evaluates_consistent_phase_decision(tmp_path)


def test_acceptance_scenario_8_legacy_representation_migration() -> None:
    """Scenario 8: Legacy migration to single authority.

    Load legacy checkpoint and integration representations. Migration must
    produce one authoritative state or explicit intervention, never merge
    contradictory truths heuristically.
    """
    # Legacy record with ls-files evidence key surfaces explicit intervention
    legacy_payload: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "diagnostic_evidence": [
            ["ls-files", "some_tracked_files"],
        ],
    }
    migrated = parse_record_payload(legacy_payload)
    assert migrated is not None
    assert migrated.diagnostic_intervention_required is True

    # Legacy record with dictionary evidence migrates cleanly
    dict_payload: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "diagnostic_evidence": {
            "work": "unstaged diff",
            "controls": "controls data",
        },
    }
    migrated_dict = parse_record_payload(dict_payload)
    assert migrated_dict is not None
    assert not migrated_dict.diagnostic_intervention_required
    evidence = dict(migrated_dict.diagnostic_evidence)
    assert evidence.get("unstaged_work") == "unstaged diff"
    assert evidence.get("commit_controls") == "controls data"

    # Legacy checkpoint loaded via Checkpoint round-trip
    step = ExecutionStep.new("development", 1, "test_step", StepOutcome.success())
    history = ExecutionHistory.new().add_step_bounded(step, limit=5)
    run_ctx = RunContext.new()
    state = PipelineState(phase="development")
    payload = CheckpointPayload(
        state=state,
        run_context=run_ctx,
        execution_history=history,
        working_dir="/tmp/test",
    )
    data = payload.to_dict()
    reconstructed = CheckpointPayload.from_dict(data)
    assert reconstructed.state.phase == "development"


def test_acceptance_scenario_9_exhausted_retry_does_not_replenish() -> None:
    """Scenario 9: Exhausted retry budget does not replenish on restart.

    Exhaust recovery, restart Ralph, and confirm no automatic attempt budget
    is silently replenished.
    """
    # Exhaust RetryProgressGuard
    guard = RetryProgressGuard(max_identical=2)
    assert not guard.record("sig1")
    assert guard.record("sig1")
    assert guard.is_exhausted

    # Snapshot to simulate durable persistence across restart
    snapshot = guard.snapshot()

    # Reconstitute on simulated restart
    restarted_guard = RetryProgressGuard.from_dict(snapshot)
    assert restarted_guard.is_exhausted is True
    # Attempting more records still immediately reports exhausted
    assert restarted_guard.record("sig1") is True

    # Test conflict budget exhaustion across restart
    prior = RebaseState(
        consecutive_conflicts=64,
        resolution_exhausted=True,
        last_outcome=RebaseOutcomeKind.INTERVENTION_REQUIRED,
    )
    durable_dict = prior.model_dump()
    restored_state = RebaseState.model_validate(durable_dict)
    assert restored_state.is_intervention_required is True

    # The action authority confirms intervention is required, not retrying
    clock = FakeClock(start=100.0)
    action = next_integration_action(restored_state, clock=clock.monotonic)
    assert action == IntegrationAction.REQUIRE_INTERVENTION
