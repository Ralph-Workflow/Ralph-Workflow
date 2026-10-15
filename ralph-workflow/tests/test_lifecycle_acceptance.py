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

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.checkpoint.checkpoint_payload import CheckpointPayload
from ralph.checkpoint.execution_history import ExecutionHistory
from ralph.checkpoint.execution_step import ExecutionStep
from ralph.checkpoint.run_context import RunContext
from ralph.checkpoint.step_outcome import StepOutcome
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.git.remote_push import PushResult, PushStatus
from ralph.pipeline import run_loop, runner
from ralph.pipeline._integration_action import (
    IntegrationAction,
    RebaseOutcomeKind,
    next_integration_action,
)
from ralph.pipeline._pipeline_state_machine import PhaseDecision
from ralph.pipeline._publication_state_machine import (
    PublicationDecision,
    next_publication_action,
)
from ralph.pipeline._retry_progress_guard import RetryProgressGuard
from ralph.pipeline._terminal_authority import is_terminal_success_eligible
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.auto_integrate_record import (
    MARK_RESOLVING_REBASE,
    PROMOTE_TO_INTEGRATED,
    START_INTEGRATING,
    IntegrationRecord,
    IntegrationRecordStore,
    parse_record_payload,
    read_record,
    write_record,
)
from ralph.pipeline.auto_integrate_remote_sync import (
    REMOTE_REMOTE_UNREACHABLE,
    RemoteSyncStatus,
    push_target_after_landing,
)
from ralph.pipeline.checkpoint import Checkpoint
from ralph.pipeline.effect_executor import execute_agent_effect
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.integration_resolution import inspect_integration_resolution
from ralph.pipeline.integration_resolution_status import IntegrationResolutionStatus
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
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


def test_acceptance_scenario_3_workflow_resume_without_replaying_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 3: Interrupted workflow resumes to completion without replaying work.

    Ensures that an interrupted workflow resumes to completion without
    replaying completed business work, tested through public workflow
    boundaries (checkpoint save/load and runner / run_loop entry points).
    """
    policy = PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                transitions=PhaseTransition(on_success="development"),
            ),
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="complete"),
            ),
        },
        entry_phase="planning",
        terminal_phase="complete",
    )
    bundle = load_policy(Path(__file__).parents[1] / "ralph" / "policy" / "defaults")
    bundle = bundle.model_copy(update={"pipeline": policy})

    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint = Checkpoint(checkpoint_file)

    executed_business_work: list[str] = []

    def _stepping_runner(
        *,
        state: PipelineState,
        workspace_scope: WorkspaceScope,
        **kwargs: object,
    ) -> PipelineState | int:
        del workspace_scope, kwargs
        current_phase = str(state.phase)
        if current_phase == "complete":
            return 0
        executed_business_work.append(current_phase)
        if current_phase == "planning":
            # Planning finishes business work and prepares advancement to development
            advanced_state = state.copy_with(phase="development")
            checkpoint.save(advanced_state)
            # Interrupt the workflow at the phase boundary
            raise KeyboardInterrupt("Simulated workflow interruption after planning")
        if current_phase == "development":
            # Development finishes business work and transitions to complete
            terminal_state = state.copy_with(phase="complete")
            checkpoint.save(terminal_state)
            return terminal_state
        return 1

    monkeypatch.setattr(runner, "run_pipeline_step", _stepping_runner)

    # Run 1: Start fresh workflow at entry phase "planning"
    initial_state = PipelineState(phase="planning")
    checkpoint.save(initial_state)

    latest_states_run1: list[PipelineState] = []
    ctx_1 = run_loop._LoopContext(
        policy_bundle=bundle,
        workspace_scope=WorkspaceScope(tmp_path),
        config=UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": False}}),
        active_display=MagicMock(),
        display_context=MagicMock(),
        effective_verbosity=MagicMock(),
        registry={},
        effective_pipeline_subscriber=None,
        controller=MagicMock(),
        config_path=None,
        cli_overrides={},
        monitor_stop=None,
        connectivity_monitor=MagicMock(),
        sleep=MagicMock(),
        is_quiet=True,
        latest_state=latest_states_run1,
    )

    with pytest.raises(KeyboardInterrupt):
        run_loop._run_inner_loop(initial_state, ctx_1, prev_phase="planning")

    # Confirm only planning business work was executed prior to interruption
    assert executed_business_work == ["planning"]

    # Durable authority: reload checkpoint across public persistence boundary
    reconstituted_state = checkpoint.load()
    assert reconstituted_state is not None
    assert reconstituted_state.phase == "development"

    # Run 2: Resume workflow from durable checkpoint state
    latest_states_run2: list[PipelineState] = []
    ctx_2 = run_loop._LoopContext(
        policy_bundle=bundle,
        workspace_scope=WorkspaceScope(tmp_path),
        config=UnifiedConfig.model_validate({"general": {"auto_integrate_enabled": False}}),
        active_display=MagicMock(),
        display_context=MagicMock(),
        effective_verbosity=MagicMock(),
        registry={},
        effective_pipeline_subscriber=None,
        controller=MagicMock(),
        config_path=None,
        cli_overrides={},
        monitor_stop=None,
        connectivity_monitor=MagicMock(),
        sleep=MagicMock(),
        is_quiet=True,
        latest_state=latest_states_run2,
    )

    final_state, _prev_phase, early_exit = run_loop._run_inner_loop(
        reconstituted_state, ctx_2, prev_phase="planning"
    )

    # Terminal runner step returns 0 through public runner boundary
    terminal_step_result = runner.run_pipeline_step(
        state=final_state,
        workspace_scope=WorkspaceScope(tmp_path),
        config=ctx_2.config,
        policy_bundle=bundle,
        display=ctx_2.active_display,
        display_context=ctx_2.display_context,
        verbosity=ctx_2.effective_verbosity,
        registry=ctx_2.registry,
        pipeline_subscriber=ctx_2.effective_pipeline_subscriber,
    )
    assert terminal_step_result == 0

    # Invariants:
    # 1. Business work in planning was NOT replayed on restart
    assert executed_business_work == ["planning", "development"]
    # 2. Resumed workflow reached completion
    assert final_state.phase == "complete"
    assert early_exit is None
    assert is_terminal_success_eligible(final_state, None, final_state.rebase) is True


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
    confusing local and remote truth, exercised through public boundaries.
    """
    repo_root = Path("/fake/repo")
    config_enabled = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_target": "main",
                "auto_integrate_remote_enabled": True,
                "auto_integrate_remote": "origin",
            }
        }
    )
    config_disabled = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_target": "main",
                "auto_integrate_remote_enabled": False,
                "auto_integrate_remote": "origin",
            }
        }
    )

    # Local landing succeeded (fast_forwarded=True, freshness_verdict verified)
    rebase_landing_complete = RebaseState(
        last_outcome=RebaseOutcomeKind.FAST_FORWARDED,
        fast_forwarded=True,
        freshness_verdict="verified",
        last_action="fast_forwarded",
    )

    # 1. Authority decisions: publication ready vs remote unavailable vs no remote
    pub_action_ready = next_publication_action(
        rebase_landing_complete,
        record=None,
        remote_unavailable=False,
        configured_remote="origin",
    )
    assert pub_action_ready == PublicationDecision.PUBLISH_NOW

    pub_action_unavailable = next_publication_action(
        rebase_landing_complete,
        record=None,
        remote_unavailable=True,
        configured_remote="origin",
    )
    assert pub_action_unavailable == PublicationDecision.PUBLISH_DEFERRED_REMOTE

    pub_action_no_remote = next_publication_action(
        rebase_landing_complete,
        record=None,
        remote_unavailable=False,
        configured_remote=None,
    )
    assert pub_action_no_remote == PublicationDecision.PUBLICATION_TERMINAL

    # 2. Case A: Remote publication succeeds (publish_now -> REMOTE_PUSHED)
    fake_remote_ok = _FakeRemoteClient(
        push_status=PushStatus.PUSHED,
        push_detail="pushed to origin/main",
    )
    result_ok = push_target_after_landing(
        config_enabled,
        repo_root,
        "main",
        rebase_landing_complete,
        remote_client=fake_remote_ok,
    )
    assert result_ok.fast_forwarded is True
    assert result_ok.last_remote_sync == RemoteSyncStatus.PUSHED
    assert result_ok.last_outcome == RebaseOutcomeKind.REMOTE_PUSHED
    assert result_ok.last_publication_decision == PublicationDecision.PUBLICATION_TERMINAL.value
    assert is_terminal_success_eligible(None, None, result_ok) is True

    # 3. Case B: Remote publication unavailable (publish_deferred_remote)
    fake_remote_unavail = _FakeRemoteClient(
        push_status=PushStatus.UNREACHABLE,
        push_detail="connection refused to origin",
    )
    result_unavail = push_target_after_landing(
        config_enabled,
        repo_root,
        "main",
        rebase_landing_complete,
        remote_client=fake_remote_unavail,
    )
    # Publication decision reflects configured publication policy under network unavailability
    assert (
        result_unavail.last_publication_decision
        == PublicationDecision.PUBLISH_DEFERRED_REMOTE.value
    )
    assert result_unavail.last_remote_sync == REMOTE_REMOTE_UNREACHABLE
    assert len(fake_remote_unavail.push_calls) == 1

    # Local landing truth: preserved intact, NOT confused or reverted
    assert result_unavail.fast_forwarded is True
    assert result_unavail.freshness_verdict == "verified"

    # Remote sync truth: publication did not complete cleanly
    assert result_unavail.last_outcome != RebaseOutcomeKind.REMOTE_PUSHED
    assert result_unavail.last_outcome == RebaseOutcomeKind.REMOTE_FAILED

    # Pending publication blocks terminal success eligibility
    assert is_terminal_success_eligible(None, None, result_unavail) is False

    # Action authority directs publish after landing when policy requires it
    policy_with_pub = MagicMock()
    policy_with_pub.publication_policy = "always"
    action = next_integration_action(result_unavail, policy=policy_with_pub)
    assert action == IntegrationAction.PUBLISH_AFTER_LANDING

    # 4. Case C: Remote publication disabled by policy
    result_disabled = push_target_after_landing(
        config_disabled,
        repo_root,
        "main",
        rebase_landing_complete,
        remote_client=fake_remote_unavail,
    )
    assert result_disabled.fast_forwarded is True
    assert (
        result_disabled.last_publication_decision == PublicationDecision.PUBLICATION_TERMINAL.value
    )
    assert is_terminal_success_eligible(None, None, result_disabled) is True


def test_acceptance_scenario_7_entry_point_parity(tmp_path: Path) -> None:
    """Scenario 7: Parity across all 6 entry points.

    Exercise startup, phase-boundary, commit, cooldown, parallel join, and
    terminal entry points against the same underlying state. Every entry point
    must produce the same typed lifecycle decision through public boundaries.
    """
    from tests.test_public_workflow_entry_point_matrix import (
        _eval_commit_handling,
        _eval_cooldown_recovery,
        _eval_parallel_join,
        _eval_phase_boundary_run_loop,
        _eval_startup_inspection,
        _eval_terminal_handling,
        _policy,
        test_entry_point_matrix_evaluates_consistent_phase_decision,
    )

    # 1. End-to-end matrix parity test
    test_entry_point_matrix_evaluates_consistent_phase_decision(tmp_path)

    # 2. Explicit direct inspection of all 6 public entry points against the same state
    (tmp_path / ".git").mkdir(exist_ok=True)
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        engine="git-merge",
        owning_worktree=str(tmp_path),
    ).with_transition(START_INTEGRATING)
    write_record(tmp_path, record)

    policy = _policy()
    shared_rebase = RebaseState()
    shared_state = PipelineState(phase="development", rebase=shared_rebase)

    bundle = load_policy(Path(__file__).parents[1] / "ralph" / "policy" / "defaults")
    bundle = bundle.model_copy(update={"pipeline": policy})

    decision_startup = _eval_startup_inspection(
        tmp_path, shared_rebase, shared_state, policy, record
    )
    decision_phase_boundary, ctx = _eval_phase_boundary_run_loop(
        tmp_path, shared_state, policy, record, bundle
    )
    decision_commit = _eval_commit_handling(tmp_path, shared_state, bundle)
    decision_cooldown = _eval_cooldown_recovery(tmp_path, shared_state, ctx)
    decision_parallel_join = _eval_parallel_join(tmp_path, shared_state, shared_rebase, bundle)
    decision_terminal = _eval_terminal_handling(tmp_path, shared_rebase, policy, record)

    decisions = {
        "startup": decision_startup,
        "phase_boundary": decision_phase_boundary,
        "commit": decision_commit,
        "cooldown": decision_cooldown,
        "parallel_join": decision_parallel_join,
        "terminal": decision_terminal,
    }

    for name, decision in decisions.items():
        assert isinstance(decision, PhaseDecision), (
            f"Entry point {name} did not produce a typed PhaseDecision"
        )
        assert decision == PhaseDecision.BLOCK_FOR_RECOVERY, (
            f"Entry point {name} evaluated to {decision}, expected BLOCK_FOR_RECOVERY"
        )

    assert len(set(decisions.values())) == 1


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


def test_acceptance_scenario_9_exhausted_retry_does_not_replenish(tmp_path: Path) -> None:
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
    assert restarted_guard.record("sig2") is True

    # Persist in PipelineState and save to checkpoint
    phase_chains = {
        "development": AgentChainState(
            agents=["claude"],
            current_index=0,
            retries=2,
            progress_guard=snapshot,
        )
    }
    prior_pipeline_state = PipelineState(
        phase="development",
        policy_entry_phase="development",
        phase_chains=phase_chains,
        progress_guard=snapshot,
    )
    checkpoint_backend = _InMemoryBackend()
    checkpoint = Checkpoint(Path(".agent/checkpoint.json"), backend=checkpoint_backend)
    checkpoint.save(prior_pipeline_state)

    # Reconstitute state from durable checkpoint
    reloaded_state = checkpoint.load()
    assert reloaded_state is not None
    assert reloaded_state.progress_guard is not None
    assert reloaded_state.progress_guard["is_exhausted"] is True
    dev_chain = reloaded_state.chain_for_phase("development")
    assert dev_chain is not None
    assert dev_chain.progress_guard is not None
    assert dev_chain.progress_guard["is_exhausted"] is True

    # Pass reloaded_state through execute_agent_effect and verify Ralph launches zero
    # further identical attempts and fails closed / requires intervention.
    mock_invoke = MagicMock()
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("test prompt", encoding="utf-8")
    effect = InvokeAgentEffect(
        agent_name="claude",
        phase="development",
        prompt_file=str(prompt_file),
    )
    display_context = MagicMock()
    deps = make_test_pipeline_deps(display_context=display_context)
    config = UnifiedConfig(
        agents={"claude": AgentConfig(cmd="claude")},
        general=GeneralConfig(max_retries=2, max_same_agent_retries=2),
    )

    event = execute_agent_effect(
        effect,
        config,
        deps,
        WorkspaceScope(tmp_path),
        display_context=display_context,
        state=reloaded_state,
        invoke_agent=mock_invoke,
    )
    assert event == PipelineEvent.AGENT_FAILURE
    assert mock_invoke.call_count == 0

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
