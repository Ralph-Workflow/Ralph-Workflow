"""Crash/restart matrix verifying recovery across durable pipeline transitions.

Injects crashes before and after the 5 core integration transitions:
1. Integration (START_INTEGRATING)
2. Continuation (MARK_PENDING_REBASE_CONTINUE)
3. Reintegration (MARK_REINTEGRATE_PENDING)
4. Landing (PROMOTE_TO_INTEGRATED)
5. Publication (Publication after landing)

Asserts each produces a known typed PhaseDecision or IntegrationAction without
replaying completed business work.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.rebase.rebase_success import RebaseSuccess
from ralph.git.remote_push import PushResult, PushStatus
from ralph.pipeline import auto_integrate as auto_integrate_module
from ralph.pipeline import auto_integrate_remote_sync as sync_module
from ralph.pipeline._integration_action import (
    IntegrationAction,
    RebaseOutcomeKind,
    next_integration_action,
)
from ralph.pipeline._owned_landing_reintegration import reintegrate_owned_landing
from ralph.pipeline._pending_rebase_continue import prepare_pending_rebase
from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
from ralph.pipeline._publication_state_machine import PublicationDecision
from ralph.pipeline.auto_integrate_rebase_merge import RebaseRunResult
from ralph.pipeline.auto_integrate_record import (
    START_INTEGRATING,
    IntegrationRecord,
    clear_record,
    read_record,
    write_record,
)
from ralph.pipeline.checkpoint import load, save
from ralph.pipeline.integration_resolution import inspect_integration_resolution
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy


def _policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="development",
                ),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
    )


class SimulatedProcessCrashError(Exception):
    """Simulated crash exception injected before or after durable write."""


def _mock_head_sha(root: Path) -> str:
    return "feat_sha_111"


def _mock_target_sha(root: Path, target: str) -> str:
    return "tgt_sha_222"


def _mock_post_head_sha(root: Path, target: str) -> str:
    return "feat_sha_integrated"


def _mock_backup_ref(*args: object) -> str:
    return "refs/rebase-backup/1"


def _mock_run_rebase(*args: object, **kwargs: object) -> RebaseRunResult:
    return RebaseRunResult(
        rebase_outcome=RebaseSuccess(),
        merge_attempted=False,
        merge_outcome=None,
        short_circuit=None,
    )


def _setup_initial_state(
    transition_name: str, tmp_path: Path
) -> tuple[IntegrationRecord | None, RebaseState, str]:
    """Set up durable on-disk state prior to invoking the production transition."""
    if transition_name == "integration":
        clear_record(tmp_path)
        rebase = RebaseState(
            last_outcome=RebaseOutcomeKind.FAST_FORWARDED,
            fast_forwarded=True,
        )
        return None, rebase, "development"

    if transition_name == "continuation":
        record = IntegrationRecord(
            phase="integrating",
            target="main",
            pre_feature_sha="feat_sha_111",
            pre_target_sha="tgt_sha_222",
            resolving_rebase=True,
            owning_worktree=str(tmp_path),
        ).with_transition(START_INTEGRATING)
        write_record(tmp_path, record)
        return record, RebaseState(), "development"

    if transition_name in ("reintegration", "landing"):
        record = IntegrationRecord(
            phase="integrating",
            target="main",
            pre_feature_sha="feat_sha_111",
            pre_target_sha="tgt_sha_222",
            owning_worktree=str(tmp_path),
        ).with_transition(START_INTEGRATING)
        write_record(tmp_path, record)
        return record, RebaseState(), "development"

    if transition_name == "publication":
        clear_record(tmp_path)
        rebase = RebaseState(
            last_outcome=RebaseOutcomeKind.PUBLISHING,
            last_publication_decision="publish_now",
            fast_forwarded=True,
        )
        return None, rebase, "development"

    raise ValueError(f"Unknown transition name: {transition_name}")


def _inject_integration_fault(
    tmp_path: Path, crash_point: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = UnifiedConfig()

    def _intercepted_integration_write(root: Path, record: IntegrationRecord) -> None:
        if record.phase == "integrating":
            if crash_point == "before":
                raise SimulatedProcessCrashError("Crashed before durable integration transition")
            write_record(root, record)
            raise SimulatedProcessCrashError("Crashed after durable integration transition")
        write_record(root, record)

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._write_record", _intercepted_integration_write
    )
    monkeypatch.setattr("ralph.pipeline.auto_integrate.get_head_sha", _mock_head_sha)
    monkeypatch.setattr("ralph.pipeline.auto_integrate.branch_sha", _mock_target_sha)

    with pytest.raises(SimulatedProcessCrashError):
        auto_integrate_module._integrate_once_owned(
            config, tmp_path, target="main", conflict_resolver=None
        )


def _inject_continuation_fault(
    tmp_path: Path, crash_point: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _intercepted_continue_write(root: Path, record: IntegrationRecord) -> None:
        if record.rebase_continue_pending:
            if crash_point == "before":
                raise SimulatedProcessCrashError("Crashed before durable continuation transition")
            record_to_write = record.model_copy(
                update={
                    "resolving_rebase": False,
                    "rebase_continue_head": "continue_sha",
                }
            )
            write_record(root, record_to_write)
            raise SimulatedProcessCrashError("Crashed after durable continuation transition")
        write_record(root, record)

    monkeypatch.setattr(
        "ralph.pipeline._pending_rebase_continue.write_record", _intercepted_continue_write
    )

    with pytest.raises(SimulatedProcessCrashError):
        prepare_pending_rebase(tmp_path, target="main")


def _inject_reintegration_fault(
    tmp_path: Path,
    crash_point: str,
    initial_record: IntegrationRecord,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _intercepted_reintegrate_write(root: Path, record: IntegrationRecord) -> None:
        if record.reintegrate_pending:
            if crash_point == "before":
                raise SimulatedProcessCrashError("Crashed before durable reintegration transition")
            write_record(root, record)
            raise SimulatedProcessCrashError("Crashed after durable reintegration transition")
        write_record(root, record)

    monkeypatch.setattr(
        "ralph.pipeline._owned_landing_reintegration.write_record", _intercepted_reintegrate_write
    )

    with pytest.raises(SimulatedProcessCrashError):
        reintegrate_owned_landing(tmp_path, initial_record, None, None, None)


def _inject_landing_fault(
    tmp_path: Path,
    crash_point: str,
    initial_record: IntegrationRecord,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = UnifiedConfig()

    def _intercepted_landing_write(root: Path, record: IntegrationRecord) -> None:
        if record.phase == "integrated":
            if crash_point == "before":
                raise SimulatedProcessCrashError("Crashed before durable landing transition")
            write_record(root, record)
            raise SimulatedProcessCrashError("Crashed after durable landing transition")
        write_record(root, record)

    monkeypatch.setattr("ralph.pipeline.auto_integrate._write_record", _intercepted_landing_write)
    monkeypatch.setattr("ralph.pipeline.auto_integrate.get_head_sha", _mock_head_sha)
    monkeypatch.setattr("ralph.pipeline.auto_integrate.branch_sha", _mock_target_sha)
    monkeypatch.setattr("ralph.pipeline.auto_integrate._create_rebase_backup_ref", _mock_backup_ref)
    monkeypatch.setattr("ralph.pipeline.auto_integrate._run_rebase_or_merge", _mock_run_rebase)
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._read_post_integration_head_sha", _mock_post_head_sha
    )

    with pytest.raises(SimulatedProcessCrashError):
        auto_integrate_module._integrate_once_owned(
            config, tmp_path, target="main", conflict_resolver=None, owned_record=initial_record
        )


def _inject_publication_fault(
    tmp_path: Path,
    crash_point: str,
    ckpt_file: Path,
    pre_crash_state: PipelineState,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = UnifiedConfig()

    def _intercepted_push(
        root: Path,
        target: str,
        *,
        remote: str | None = None,
        record: RebaseState | None = None,
        **_kwargs: object,
    ) -> tuple[PushResult | None, PublicationDecision]:
        if crash_point == "before":
            raise SimulatedProcessCrashError("Crashed before durable publication transition")
        return (
            PushResult(status=PushStatus.PUSHED, remote="origin", branch="main"),
            PublicationDecision.PUBLISH_NOW,
        )

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_remote_sync.push_single_remote", _intercepted_push
    )

    def _run_publication() -> None:
        pub_state = sync_module.push_target_after_landing(
            config, tmp_path, "main", pre_crash_state.rebase
        )
        if pub_state.last_outcome == RebaseOutcomeKind.REMOTE_PUSHED:
            persisted_state = pre_crash_state.copy_with(phase="complete", rebase=pub_state)
            save(persisted_state, ckpt_file)
            if crash_point == "after":
                raise SimulatedProcessCrashError("Crashed after durable publication transition")

    with pytest.raises(SimulatedProcessCrashError):
        _run_publication()


def _execute_transition_under_fault_injection(
    transition_name: str,
    crash_point: str,
    tmp_path: Path,
    ckpt_file: Path,
    pre_crash_state: PipelineState,
    initial_record: IntegrationRecord | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Execute real production transition call with before/after crash fault injection."""
    if transition_name == "integration":
        _inject_integration_fault(tmp_path, crash_point, monkeypatch)
    elif transition_name == "continuation":
        _inject_continuation_fault(tmp_path, crash_point, monkeypatch)
    elif transition_name == "reintegration":
        assert initial_record is not None
        _inject_reintegration_fault(tmp_path, crash_point, initial_record, monkeypatch)
    elif transition_name == "landing":
        assert initial_record is not None
        _inject_landing_fault(tmp_path, crash_point, initial_record, monkeypatch)
    elif transition_name == "publication":
        _inject_publication_fault(tmp_path, crash_point, ckpt_file, pre_crash_state, monkeypatch)
    else:
        raise ValueError(f"Unknown transition name: {transition_name}")


@pytest.mark.parametrize(
    ("transition_name", "crash_point", "expected_action", "expected_decision"),
    [
        (
            "integration",
            "before",
            IntegrationAction.COMPLETE_WITH_PROOF,
            PhaseDecision.REPORT_TERMINAL_SUCCESS,
        ),
        (
            "integration",
            "after",
            IntegrationAction.REINTEGRATE_AGAINST_TARGET,
            PhaseDecision.BLOCK_FOR_RECOVERY,
        ),
        (
            "continuation",
            "before",
            IntegrationAction.RESOLVE_ACTIVE_OPERATION,
            PhaseDecision.BLOCK_FOR_RECOVERY,
        ),
        (
            "continuation",
            "after",
            IntegrationAction.CONTINUE_GIT_OPERATION,
            PhaseDecision.BLOCK_FOR_RECOVERY,
        ),
        (
            "reintegration",
            "before",
            IntegrationAction.REINTEGRATE_AGAINST_TARGET,
            PhaseDecision.BLOCK_FOR_RECOVERY,
        ),
        (
            "reintegration",
            "after",
            IntegrationAction.REINTEGRATE_AGAINST_TARGET,
            PhaseDecision.BLOCK_FOR_RECOVERY,
        ),
        (
            "landing",
            "before",
            IntegrationAction.REINTEGRATE_AGAINST_TARGET,
            PhaseDecision.BLOCK_FOR_RECOVERY,
        ),
        (
            "landing",
            "after",
            IntegrationAction.LAND_TARGET,
            PhaseDecision.BLOCK_FOR_RECOVERY,
        ),
        (
            "publication",
            "before",
            IntegrationAction.PUBLISH_AFTER_LANDING,
            PhaseDecision.BLOCK_FOR_RECOVERY,
        ),
        (
            "publication",
            "after",
            IntegrationAction.COMPLETE_WITH_PROOF,
            PhaseDecision.REPORT_TERMINAL_SUCCESS,
        ),
    ],
)
def test_crash_restart_matrix_routes_authoritatively(
    tmp_path: Path,
    transition_name: str,
    crash_point: str,
    expected_action: IntegrationAction,
    expected_decision: PhaseDecision,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _policy()

    ckpt_dir = tmp_path / ".agent"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    ckpt_file = ckpt_dir / "checkpoint.json"

    initial_record, initial_rebase, initial_phase = _setup_initial_state(transition_name, tmp_path)

    # Establish business work performed before crash and persist pipeline checkpoint
    pre_crash_state = PipelineState(
        phase=initial_phase,
        loop_iterations={"development": 2},
        checkpoint_saved_count=1,
        rebase=initial_rebase,
    )
    save(pre_crash_state, ckpt_file)

    # Execute production transition with fault injection interrupting before or after durable write
    _execute_transition_under_fault_injection(
        transition_name=transition_name,
        crash_point=crash_point,
        tmp_path=tmp_path,
        ckpt_file=ckpt_file,
        pre_crash_state=pre_crash_state,
        initial_record=initial_record,
        monkeypatch=monkeypatch,
    )

    # Simulate process restart: reload checkpoint and durable record from disk
    reloaded_state = load(ckpt_file)
    assert reloaded_state is not None
    # Verify completed business work was preserved across restart without replay
    assert reloaded_state.loop_iterations.get("development") == 2
    assert reloaded_state.checkpoint_saved_count == 1
    expected_reloaded_phase = (
        "complete"
        if (transition_name == "publication" and crash_point == "after")
        else "development"
    )
    assert reloaded_state.phase == expected_reloaded_phase

    restarted_record = read_record(tmp_path)

    # Invoke public recovery/startup boundary
    resolution_verdict = inspect_integration_resolution(tmp_path, reloaded_state.rebase)
    assert resolution_verdict is not None
    if expected_decision == PhaseDecision.REPORT_TERMINAL_SUCCESS:
        assert resolution_verdict.dispatch_allowed
    elif restarted_record is not None:
        assert not resolution_verdict.dispatch_allowed

    # Assert authority outcomes from durable state without replaying completed work
    if restarted_record is not None:
        action = next_integration_action(restarted_record, policy=policy)
    else:
        action = next_integration_action(reloaded_state.rebase, policy=policy)

    assert action == expected_action

    decision = decide_phase_transition(
        reloaded_state,
        policy,
        integration_record=restarted_record,
        rebase_state=reloaded_state.rebase,
    )
    assert decision == expected_decision
