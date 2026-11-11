"""Restart behavior of an exhausted integration-diagnostic repair budget.

The diagnostic-repair budget is a REPOSITORY-state bound: once the
durable record reaches the cap it carries the typed "diagnostic repair
budget spent" outcome, and a restart must NOT replenish the counter --
the restarted pass offers no agent either.

The inspected root is a plain directory that is not a Git repository, so
every Git query the diagnostic makes already answers "not a repository".
This unit keeps that premise but stops paying for it in process
spawns: the Git queries are answered by a deterministic double and the
evidence collector is the in-memory one, so the unit's cost no longer
scales with the host's CPU contention (it used to spawn 29 ``git``
processes, which is what pushed it past the per-test CPU budget).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ralph.git.git_run_result import GitRunResult
from ralph.pipeline._integration_diagnostic import handoff_integration_diagnostic
from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    read_record,
    write_record,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.operation_evidence import InMemoryOperationEvidenceCollector
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from tests._resolver_controller import make_resolver_controller

#: The exit status ``git`` reports when it cannot answer at all because the
#: directory it was pointed at is not a repository. The diagnostic's
#: completion proof reads it as "HEAD is unreadable" / "marker status is
#: UNKNOWN", so the proof refuses to verify and the attempt is charged.
_GIT_NOT_A_REPOSITORY_RETURNCODE = 128

#: The modules whose ``run_git`` binding the diagnostic's Git queries go
#: through. The bound runner is looked up per module (some import it at
#: module level, some inside the function), so every binding the
#: diagnostic reaches is replaced.
_GIT_RUNNER_MODULES = (
    "ralph.git.subprocess_runner",
    "ralph.git.merge",
    "ralph.pipeline.integration_observation",
)


def _not_a_git_repository(
    args: tuple[str, ...] | list[str],
    **_kwargs: object,
) -> GitRunResult:
    """Answer every Git query the way a non-repository directory does.

    The double replaces the real ``git`` subprocess so a unit that only
    asserts what the diagnostic DOES with an unverifiable repository keeps
    that premise without spawning a process. The answers are the ones the
    inspected root already produced, so no observation the unit relies on
    changes -- only the process spawn is gone.
    """
    return GitRunResult(
        args=tuple(args),
        returncode=_GIT_NOT_A_REPOSITORY_RETURNCODE,
        stdout="",
        stderr="fatal: not a git repository (or any of the parent directories): .git",
    )


def _evidence_collector() -> InMemoryOperationEvidenceCollector:
    """Return the evidence collector used instead of the subprocess one.

    The default collector reads the operation-scoped evidence through
    ``git``; the injected one answers from memory. The diagnostic reads
    the snapshot only to detect an unreachable marker object, and the
    collected evidence of a non-repository root never carries one, so the
    injected collector leaves the pre-pass on the same branch.
    """
    return InMemoryOperationEvidenceCollector()


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


def test_exhaustion_restart_prevents_budget_replenishment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The handoff consults live Git three times -- the evidence snapshot,
    # the owner's observation and the operation-completion proof. All
    # three are replaced here by deterministic doubles that answer the
    # way this non-repository root already answered, so the unit spawns
    # no process and its cost does not scale with the host.
    for module in _GIT_RUNNER_MODULES:
        monkeypatch.setattr(f"{module}.run_git", _not_a_git_repository)

    # 1. Create a record that has reached max diagnostic repairs (3 attempts)
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        merge_commit_pending=True,
        merge_commit_repair_attempts=3,
        owning_worktree=str(tmp_path),
    )
    write_record(tmp_path, record)
    saved_before = read_record(tmp_path)
    assert saved_before is not None
    assert saved_before.merge_commit_repair_attempts == 3
    assert saved_before.diagnostic_intervention_required is False

    # 2. Invoke genuine handoff_integration_diagnostic entry point with exhausted attempts
    handoff_success = handoff_integration_diagnostic(
        controller=make_resolver_controller(),
        root=tmp_path,
        record=record,
        failure="diagnostic failure evidence",
        agents=["test-diagnostic-agent"],
        invoke=lambda *_: True,
        repair_attempt_cap=3,
        collector=_evidence_collector(),
    )
    assert handoff_success is False

    # Fault-injection checkpoint: confirm record was written to disk with budget spent reason
    saved_after = read_record(tmp_path)
    assert saved_after is not None
    assert saved_after.diagnostic_intervention_required is False
    assert "diagnostic repair budget spent" in (saved_after.repair_cap_reason or "")
    assert saved_after.merge_commit_repair_attempts == 3

    # 3. Simulate process restart by reloading from disk with exhausted record
    restarted_record = read_record(tmp_path)
    assert restarted_record is not None
    assert restarted_record.merge_commit_repair_attempts == 3
    assert restarted_record.diagnostic_intervention_required is False

    # Restart public recovery with the reloaded exhausted record; assert zero agent calls occur
    mock_invoke = MagicMock()
    handoff_restart = handoff_integration_diagnostic(
        controller=make_resolver_controller(),
        root=tmp_path,
        record=restarted_record,
        failure="diagnostic failure after restart",
        agents=["test-diagnostic-agent"],
        invoke=mock_invoke,
        repair_attempt_cap=3,
        collector=_evidence_collector(),
    )
    assert handoff_restart is False
    mock_invoke.assert_not_called()

    # Confirm no attempt budget replenishment: attempts counter is still 3, not reset or incremented
    after_restart = read_record(tmp_path)
    assert after_restart is not None
    assert after_restart.merge_commit_repair_attempts == 3
    assert after_restart.diagnostic_intervention_required is False

    # 4. Verify reducer with clean state yields BLOCK_FOR_RECOVERY from durable record
    state = PipelineState(phase="development", rebase=RebaseState())
    policy = _policy()
    new_state, _ = reduce(
        state, PipelineEvent.AGENT_SUCCESS, policy, integration_record=after_restart
    )
    assert new_state.rebase.last_decision == PhaseDecision.BLOCK_FOR_RECOVERY.value

    # Direct authority evaluation also confirms BLOCK_FOR_RECOVERY
    decision = decide_phase_transition(
        state,
        policy,
        integration_record=after_restart,
        rebase_state=state.rebase,
    )
    assert decision == PhaseDecision.BLOCK_FOR_RECOVERY
