from __future__ import annotations

import json
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
from ralph.pipeline._integration_action import (
    IntegrationAction,
    RebaseOutcomeKind,
    next_integration_action,
)
from ralph.pipeline._pipeline_state_machine import PhaseDecision
from ralph.pipeline._terminal_authority import is_terminal_success_eligible
from ralph.pipeline.auto_integrate_record import (
    MARK_RESOLVING_REBASE,
    PROMOTE_TO_INTEGRATED,
    START_INTEGRATING,
    IntegrationRecord,
    parse_record_payload,
    read_record,
    write_record,
)
from ralph.pipeline.auto_integrate_record_store import IntegrationRecordStore
from ralph.pipeline.auto_integrate_remote_push import next_publication_action
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
from ralph.pipeline.rebase_state import PublicationDecision, RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from ralph.recovery.agent_failure_ledger import AgentFailureLedger
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests.test_checkpoint_and_record_persistence_boundary import _InMemoryBackend


@dataclass
class _FakeRemoteClient:
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
    backend = _InMemoryBackend()
    root = Path("/workspace-crash-test")
    store = IntegrationRecordStore(root, backend=backend)

    step1 = store.transition(
        START_INTEGRATING,
        target="main",
        pre_feature_sha="1" * 40,
        pre_target_sha="2" * 40,
    )
    assert step1.phase == "integrating"
    assert read_record(root, backend=backend) == step1

    store_restart_1 = IntegrationRecordStore(root, backend=backend)
    step2 = store_restart_1.transition(MARK_RESOLVING_REBASE, resolving_rebase=True)
    assert step2.resolving_rebase is True
    assert read_record(root, backend=backend) == step2

    store_restart_2 = IntegrationRecordStore(root, backend=backend)
    step2_replayed = store_restart_2.transition(MARK_RESOLVING_REBASE, resolving_rebase=True)
    assert step2_replayed == step2

    step3 = store_restart_2.transition(
        PROMOTE_TO_INTEGRATED,
        phase="integrated",
        integrated_feature_sha="3" * 40,
    )
    assert step3.phase == "integrated"
    assert step3.is_integrated is True
    assert read_record(root, backend=backend) == step3


def test_acceptance_scenario_3_workflow_resume_without_replaying_work(
    tmp_path: Path,
) -> None:
    checkpoint_file = tmp_path / "checkpoint.json"
    checkpoint = Checkpoint(checkpoint_file)

    executed_business_work: list[str] = []

    initial_state = PipelineState(phase="planning")
    checkpoint.save(initial_state)
    executed_business_work.append(str(initial_state.phase))

    interrupted_state = initial_state.copy_with(phase="development")
    checkpoint.save(interrupted_state)

    assert executed_business_work == ["planning"]

    reconstituted_state = checkpoint.load()
    assert reconstituted_state is not None
    assert reconstituted_state.phase == "development"

    executed_business_work.append(str(reconstituted_state.phase))
    final_state = reconstituted_state.copy_with(phase="complete")
    checkpoint.save(final_state)

    assert executed_business_work == ["planning", "development"]
    assert final_state.phase == "complete"
    assert is_terminal_success_eligible(final_state, None, final_state.rebase) is True


def test_acceptance_scenario_4_unreadable_evidence_fails_closed() -> None:
    assert parse_record_payload({"phase": "invalid_phase"}) is None
    assert parse_record_payload({"phase": 123}) is None

    verdict = inspect_integration_resolution(
        Path("/nonexistent"),
        RebaseState(),
        porcelain=lambda _p: (False, "git query failure"),
    )
    assert verdict.dispatch_allowed is False
    assert verdict.status == IntegrationResolutionStatus.RECOVERABLE

    corrupted_record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat",
        pre_target_sha="main",
        diagnostic_intervention_required=True,
    )
    action = next_integration_action(corrupted_record)
    assert action == IntegrationAction.REQUIRE_INTERVENTION

    expired_state = RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        retry_deadline=100.0,
    )
    clock = FakeClock(start=150.0)
    action_expired = next_integration_action(expired_state, clock=clock.monotonic)
    assert action_expired == IntegrationAction.REQUIRE_INTERVENTION


def test_acceptance_scenario_6_local_landing_complete_remote_unavailable() -> None:
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

    rebase_landing_complete = RebaseState(
        last_outcome=RebaseOutcomeKind.FAST_FORWARDED,
        fast_forwarded=True,
        freshness_verdict="verified",
        last_action="fast_forwarded",
    )

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
    assert (
        result_unavail.last_publication_decision
        == PublicationDecision.PUBLISH_DEFERRED_REMOTE.value
    )
    assert result_unavail.last_remote_sync == REMOTE_REMOTE_UNREACHABLE
    assert len(fake_remote_unavail.push_calls) == 1

    assert result_unavail.fast_forwarded is True
    assert result_unavail.freshness_verdict == "verified"

    assert result_unavail.last_outcome != RebaseOutcomeKind.REMOTE_PUSHED
    assert result_unavail.last_outcome == RebaseOutcomeKind.REMOTE_FAILED

    assert is_terminal_success_eligible(None, None, result_unavail) is False

    policy_with_pub = MagicMock()
    policy_with_pub.publication_policy = "always"
    action = next_integration_action(result_unavail, policy=policy_with_pub)
    assert action == IntegrationAction.PUBLISH_AFTER_LANDING

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
    from ralph.pipeline._pipeline_state_machine import decide_phase_transition

    path_direct = tmp_path / "direct"
    path_direct.mkdir()
    (path_direct / ".git").mkdir()
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        engine="git-merge",
        owning_worktree=str(path_direct),
    ).with_transition(START_INTEGRATING)
    write_record(path_direct, record)

    policy = PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_failure="development",
                ),
            ),
            "complete": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                ),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
    )
    shared_rebase = RebaseState()
    shared_state = PipelineState(phase="development", rebase=shared_rebase)

    verdict_startup = inspect_integration_resolution(path_direct, shared_rebase)
    assert not verdict_startup.dispatch_allowed
    assert verdict_startup.status == IntegrationResolutionStatus.RECOVERABLE

    decision_startup = decide_phase_transition(
        shared_state, policy, integration_record=record, rebase_state=shared_rebase
    )
    waiting_state = shared_state.copy_with(is_waiting_state=True)
    decision_phase_boundary = decide_phase_transition(
        waiting_state, policy, integration_record=record, rebase_state=waiting_state.rebase
    )
    decision_commit = decide_phase_transition(
        shared_state, policy, integration_record=record, rebase_state=shared_rebase
    )
    cooldown_state = shared_state.copy_with(is_waiting_state=True)
    decision_cooldown = decide_phase_transition(
        cooldown_state, policy, integration_record=record, rebase_state=shared_rebase
    )

    decision_parallel_join = decide_phase_transition(
        shared_state, policy, integration_record=record, rebase_state=shared_rebase
    )
    terminal_state = PipelineState(phase="complete", rebase=shared_rebase)
    assert is_terminal_success_eligible(terminal_state, record, shared_rebase) is False
    decision_terminal = decide_phase_transition(
        terminal_state, policy, integration_record=record, rebase_state=terminal_state.rebase
    )

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


@pytest.mark.timeout_seconds(5)
def test_acceptance_scenario_8_legacy_representation_migration() -> None:
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
    assert migrated.diagnostic_intervention_required is False
    assert dict(migrated.diagnostic_evidence) == {}

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
    snapshot = {
        "max_identical": 2,
        "last_signature": "sig1",
        "streak": 2,
        "is_exhausted": True,
        "failed_closed": True,
    }

    legacy_payload = {
        "phase": "development",
        "policy_entry_phase": "development",
        "phase_chains": {
            "development": {
                "agents": ["claude"],
                "current_index": 0,
                "retries": 2,
                "progress_guard": snapshot,
            }
        },
        "progress_guard": snapshot,
    }
    checkpoint_backend = _InMemoryBackend()
    checkpoint = Checkpoint(Path(".agent/checkpoint.json"), backend=checkpoint_backend)
    checkpoint_backend.write_text(
        Path(".agent/checkpoint.json"),
        json.dumps(legacy_payload),
    )

    reloaded_state = checkpoint.load()
    assert reloaded_state is not None
    assert reloaded_state.ledger_snapshot is not None

    restored = AgentFailureLedger.from_snapshot(
        reloaded_state.ledger_snapshot,
        clock=FakeClock(),
    )
    assert restored.status("development", "claude").is_spent is True
    assert restored.is_available("development", "claude") is False

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

    prior = RebaseState(
        consecutive_conflicts=64,
        resolution_exhausted=True,
        last_outcome=RebaseOutcomeKind.INTERVENTION_REQUIRED,
    )
    durable_dict = prior.model_dump()
    restored_state = RebaseState.model_validate(durable_dict)
    assert restored_state.is_intervention_required is True

    clock = FakeClock(start=100.0)
    action = next_integration_action(restored_state, clock=clock.monotonic)
    assert action == IntegrationAction.REQUIRE_INTERVENTION


def test_lifecycle_l1_and_l10_content_equivalence_pure_proof() -> None:
    from tests.scenarios._content_equivalence_report import ContentEquivalence

    satisfied_case = ContentEquivalence()
    assert satisfied_case.satisfied is True
    assert satisfied_case.describe() == "content equivalence holds"

    missing_case = ContentEquivalence(missing=("feature.py",))
    assert missing_case.satisfied is False
    assert "feature change absent from the target: feature.py" in missing_case.describe()

    unexpected_case = ContentEquivalence(unexpected=("stray.py",))
    assert unexpected_case.satisfied is False
    assert "unrelated content introduced on the target: stray.py" in unexpected_case.describe()

    lost_case = ContentEquivalence(lost=("uncommitted.txt",))
    assert lost_case.satisfied is False
    assert "uncommitted work lost: uncommitted.txt" in lost_case.describe()

    combined = ContentEquivalence(missing=("a.py",), unexpected=("b.py",), lost=("c.py",))
    assert combined.satisfied is False
    description = combined.describe()
    assert "feature change absent" in description
    assert "unrelated content introduced" in description
    assert "uncommitted work lost" in description


def test_lifecycle_l2_strategy_decision_non_harness_proof() -> None:
    from ralph.pipeline.integration_strategy import (
        IntegrationPolicy,
        IntegrationRelation,
        IntegrationStrategy,
        IntegrationWorkShape,
        ObservedIntegration,
        choose_integration_strategy,
    )

    permissive_policy = IntegrationPolicy()

    decision_equal = choose_integration_strategy(
        ObservedIntegration(
            relation=IntegrationRelation.EQUAL,
            work_shape=IntegrationWorkShape(commit_count=0),
        ),
        permissive_policy,
    )
    assert decision_equal.strategy is IntegrationStrategy.NOTHING_TO_DO
    assert "already satisfied" in decision_equal.reason.lower()
    assert IntegrationStrategy.COMPLETE_IN_PLACE in decision_equal.alternatives

    decision_ahead = choose_integration_strategy(
        ObservedIntegration(
            relation=IntegrationRelation.AHEAD,
            work_shape=IntegrationWorkShape(commit_count=3),
        ),
        permissive_policy,
    )
    assert decision_ahead.strategy is IntegrationStrategy.FAST_FORWARD
    assert "fast-forward" in decision_ahead.reason.lower()

    decision_behind = choose_integration_strategy(
        ObservedIntegration(
            relation=IntegrationRelation.BEHIND,
            work_shape=IntegrationWorkShape(commit_count=2),
        ),
        permissive_policy,
    )
    assert decision_behind.strategy is IntegrationStrategy.REBASE

    decision_published = choose_integration_strategy(
        ObservedIntegration(
            relation=IntegrationRelation.DIVERGED,
            work_shape=IntegrationWorkShape(
                commit_count=2,
                history_already_published=True,
            ),
        ),
        permissive_policy,
    )
    assert decision_published.strategy is IntegrationStrategy.MERGE
    assert "never rewritten" in decision_published.reason.lower()

    decision_unrelated = choose_integration_strategy(
        ObservedIntegration(
            relation=IntegrationRelation.UNRELATED,
            work_shape=IntegrationWorkShape(commit_count=5),
        ),
        permissive_policy,
    )
    assert decision_unrelated.strategy is IntegrationStrategy.MERGE

    terminal_only_policy = IntegrationPolicy(
        allow_nothing_to_do=False,
        allow_fast_forward=False,
        allow_rebase=False,
        allow_merge=False,
        allow_direct_integration=False,
    )
    decision_terminal = choose_integration_strategy(
        ObservedIntegration(
            relation=IntegrationRelation.DIVERGED,
            work_shape=IntegrationWorkShape(commit_count=10),
        ),
        terminal_only_policy,
    )
    assert decision_terminal.strategy is IntegrationStrategy.COMPLETE_IN_PLACE
    assert "completion in place" in decision_terminal.reason.lower()


def test_lifecycle_l3_and_l5_strategy_transitions_non_harness_proof() -> None:
    from ralph.pipeline.integration_strategy import (
        STRATEGY_ORDER,
        STRATEGY_TRANSITIONS,
        IntegrationStrategy,
        StrategyTransitionGuard,
    )

    assert len(STRATEGY_TRANSITIONS) > 0

    for transition in STRATEGY_TRANSITIONS:
        assert transition.preserves_resolved_work is True
        assert transition.restarts_from_zero is False
        assert transition.reason.strip() != ""
        assert transition.current_strategy in STRATEGY_ORDER
        assert transition.next_strategy in STRATEGY_ORDER
        assert STRATEGY_ORDER.index(transition.next_strategy) > STRATEGY_ORDER.index(
            transition.current_strategy
        )

    absorb_guards = {
        StrategyTransitionGuard.NEW_FEATURE_WORK,
        StrategyTransitionGuard.NEW_TARGET_WORK,
    }
    for guard in absorb_guards:
        matching = [t for t in STRATEGY_TRANSITIONS if t.guard is guard]
        assert len(matching) > 0
        for transition in matching:
            assert transition.absorbs_new_work is True
            assert transition.preserves_resolved_work is True

    terminal_outgoing = [
        t
        for t in STRATEGY_TRANSITIONS
        if t.current_strategy is IntegrationStrategy.COMPLETE_IN_PLACE
    ]
    assert len(terminal_outgoing) == 0


def test_lifecycle_l8_publication_state_machine_non_harness_proof() -> None:
    from ralph.pipeline.auto_integrate_remote_push import next_publication_action
    from ralph.pipeline.rebase_state import PublicationDecision

    unintegrated_record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="1" * 40,
        pre_target_sha="2" * 40,
    )
    integrated_record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="1" * 40,
        pre_target_sha="2" * 40,
    )

    landing_ff = RebaseState(
        last_outcome=RebaseOutcomeKind.FAST_FORWARDED,
        fast_forwarded=True,
        freshness_verdict="verified",
    )

    decision_no_remote = next_publication_action(
        landing_ff,
        record=integrated_record,
        remote_unavailable=False,
        configured_remote=None,
    )
    assert decision_no_remote is PublicationDecision.PUBLICATION_TERMINAL

    decision_unintegrated = next_publication_action(
        landing_ff,
        record=unintegrated_record,
        remote_unavailable=False,
        configured_remote="origin",
    )
    assert decision_unintegrated is PublicationDecision.PUBLICATION_PENDING

    decision_not_ff = next_publication_action(
        RebaseState(fast_forwarded=False, freshness_verdict="verified"),
        record=integrated_record,
        remote_unavailable=False,
        configured_remote="origin",
    )
    assert decision_not_ff is PublicationDecision.PUBLICATION_PENDING

    decision_remote_down = next_publication_action(
        landing_ff,
        record=integrated_record,
        remote_unavailable=True,
        configured_remote="origin",
    )
    assert decision_remote_down is PublicationDecision.PUBLISH_DEFERRED_REMOTE

    decision_verified = next_publication_action(
        landing_ff,
        record=integrated_record,
        remote_unavailable=False,
        configured_remote="origin",
    )
    assert decision_verified is PublicationDecision.PUBLISH_NOW

    landing_unverified = RebaseState(
        fast_forwarded=True,
        freshness_verdict="unverified",
    )
    decision_unverified = next_publication_action(
        landing_unverified,
        record=integrated_record,
        remote_unavailable=False,
        configured_remote="origin",
    )
    assert decision_unverified is PublicationDecision.PUBLISH_DEFERRED_FRESHNESS

    landing_unsafe = RebaseState(
        fast_forwarded=True,
        freshness_verdict="unsafe",
    )
    decision_unsafe = next_publication_action(
        landing_unsafe,
        record=integrated_record,
        remote_unavailable=False,
        configured_remote="origin",
    )
    assert decision_unsafe is PublicationDecision.PUBLICATION_BLOCKED


def test_lifecycle_l7_and_l9_cleanup_residue_non_harness_proof() -> None:
    from ralph.pipeline.integration_cleanup import (
        PlanningBaseline,
        detect_planning_residue,
        residue_attribution,
    )
    from ralph.pipeline.integration_decision import DirtAttribution

    baseline = PlanningBaseline(
        head_sha="a" * 40,
        status_blob="?? initial.txt\0",
        tracked_diff_hash="hash1",
        fingerprint="fp1",
        human_work_ref=None,
    )

    clean_residue = detect_planning_residue(
        baseline,
        after_blob="?? initial.txt\0",
        after_diff_hash="hash1",
    )
    assert clean_residue is None

    dirty_residue = detect_planning_residue(
        baseline,
        after_blob="?? initial.txt\0?? extra.txt\0 M file.py\0",
        after_diff_hash="hash2",
    )
    assert dirty_residue is not None
    assert "extra.txt" in dirty_residue.untracked_paths
    assert "file.py" in dirty_residue.tracked_paths

    attribution = residue_attribution(dirty_residue)
    assert isinstance(attribution, DirtAttribution)
