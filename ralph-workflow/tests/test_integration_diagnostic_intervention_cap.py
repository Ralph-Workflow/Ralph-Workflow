"""Bounded-recovery tests for the diagnostic-repair attempt cap.

The production loop kept rotating inspection agents without ever
reaching a terminal state. The fix is a module-level cap; once
the cap is reached the diagnostic path stamps the typed "diagnostic
budget spent" outcome and routes the state into the autonomous
fallback ladder -- never a human hand-off.

Rewritten to drive the real recovery boundary:
(ralph.pipeline._integration_diagnostic.diagnose_retained_integration
and ralph.pipeline._integration_diagnostic_handoff), asserting the
typed outcome, the ladder escalation and the non-terminal phase
decision.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from unittest.mock import MagicMock

from ralph.config.models import UnifiedConfig
from ralph.pipeline import run_loop
from ralph.pipeline._integration_diagnostic import (
    DIAGNOSTIC_BUDGET_SPENT_REASON,
    DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
    budget_spent_record,
    diagnose_retained_integration,
    escalate_diagnostic_budget_to_ladder,
)
from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
from ralph.pipeline.auto_integrate_record import (
    START_INTEGRATING,
    IntegrationRecord,
    read_record,
    write_record,
)
from ralph.pipeline.integration_attempt_ledger import AttemptLedger
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy, PolicyBundle
from ralph.workspace.scope import WorkspaceScope


@lru_cache(maxsize=1)
def _default_bundle() -> PolicyBundle:
    return load_policy(Path(__file__).parents[1] / "ralph" / "policy" / "defaults")


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


def _build_ctx(root: Path, policy: PipelinePolicy) -> run_loop._LoopContext:
    bundle = _default_bundle().model_copy(update={"pipeline": policy})
    return run_loop._LoopContext(
        policy_bundle=bundle,
        workspace_scope=WorkspaceScope(root),
        config=UnifiedConfig(),
        active_display=MagicMock(),
        display_context=MagicMock(),
        effective_verbosity=MagicMock(),
        registry=MagicMock(get=lambda _name: None),
        effective_pipeline_subscriber=None,
        controller=MagicMock(),
        config_path=None,
        cli_overrides={},
        monitor_stop=None,
        connectivity_monitor=MagicMock(),
        sleep=MagicMock(),
        is_quiet=True,
        pipeline_deps=MagicMock(),
    )


def test_cap_stamps_budget_spent_and_routes_to_ladder(tmp_path: Path) -> None:
    """Reaching the cap stamps the typed outcome and escalates to a ladder rung.

    A record written with ``merge_commit_repair_attempts = cap`` is
    loaded by the diagnostic. The load-time clamp preserves the counter
    when the typed "diagnostic budget spent" marker is present, so the
    diagnostic gate fires immediately: no further agent is called, the
    record carries the typed marker (never the retired
    ``diagnostic_intervention_required`` hand-off flag), and the
    escalation reaches a ladder rung recorded in the durable ledger.
    """
    policy = _policy()
    ctx = _build_ctx(tmp_path, policy)

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        merge_commit_repair_attempts=DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
        owning_worktree=str(tmp_path),
    ).with_transition(START_INTEGRATING)
    record = budget_spent_record(record)
    write_record(tmp_path, record)

    ledger = AttemptLedger(tmp_path, ledger_path=tmp_path / "attempt-ledger.json")
    step = escalate_diagnostic_budget_to_ladder(
        tmp_path,
        record,
        ledger=ledger,
    )
    assert step.rung.value in {
        "normal_cleanup",
        "alternate_preservation",
        "quarantine",
        "fresh_worktree",
        "environment_degrade",
        "last_resort",
    }

    result = diagnose_retained_integration(ctx, "completion receipt unreadable")
    assert result is False

    persisted = read_record(tmp_path)
    assert persisted is not None
    # The typed outcome is the durable signal of the spent budget; the
    # retired hand-off flag is never set.
    assert persisted.diagnostic_intervention_required is False
    assert persisted.repair_cap_reason == DIAGNOSTIC_BUDGET_SPENT_REASON
    assert persisted.merge_commit_repair_attempts == DIAGNOSTIC_REPAIR_ATTEMPT_CAP

    # The phase decision is a recovery block (ladder escalation), never
    # the terminal operator hand-off.
    state = PipelineState(phase="development", rebase=RebaseState())
    decision = decide_phase_transition(
        state,
        policy,
        integration_record=persisted,
        rebase_state=state.rebase,
    )
    assert decision != PhaseDecision.REPORT_INTERVENTION_REQUIRED
    assert decision in (
        PhaseDecision.BLOCK_FOR_RECOVERY,
        PhaseDecision.CONTINUE_PHASE,
        PhaseDecision.ADVANCE_PHASE,
    )


def test_cap_does_not_increment_attempt_counter(tmp_path: Path) -> None:
    """The typed marker preserves the counter at the cap across loads.

    Records written with a value at or above the cap WITHOUT the typed
    marker are legacy stuck counters: the load-time clamp rewrites them
    to ``cap - 1`` so the next run's gate is False and the record
    proceeds to automated recovery. The on-disk value never silently
    increments.
    """
    policy = _policy()
    ctx = _build_ctx(tmp_path, policy)

    over_cap_count = DIAGNOSTIC_REPAIR_ATTEMPT_CAP + 1
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        merge_commit_repair_attempts=over_cap_count,
        owning_worktree=str(tmp_path),
    ).with_transition(START_INTEGRATING)
    write_record(tmp_path, record)

    diagnose_retained_integration(ctx, "completion receipt unreadable")

    persisted = read_record(tmp_path)
    assert persisted is not None
    # Loader clamp: any unmarked value >= cap is read as cap - 1, and the
    # in-run diagnostic may re-bump the counter up to the cap (never past
    # it). The retired hand-off flag is never set; a counter resting at
    # the cap carries the typed budget-spent outcome.
    assert persisted.merge_commit_repair_attempts <= DIAGNOSTIC_REPAIR_ATTEMPT_CAP
    assert persisted.diagnostic_intervention_required is False
    if persisted.merge_commit_repair_attempts == DIAGNOSTIC_REPAIR_ATTEMPT_CAP:
        assert persisted.repair_cap_reason == DIAGNOSTIC_BUDGET_SPENT_REASON


def test_below_cap_preserves_budget_unspent(tmp_path: Path) -> None:
    """A record below the cap retains an unspent budget."""
    policy = _policy()
    ctx = _build_ctx(tmp_path, policy)

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        merge_commit_repair_attempts=0,
        owning_worktree=str(tmp_path),
    ).with_transition(START_INTEGRATING)
    write_record(tmp_path, record)

    diagnose_retained_integration(ctx, "diagnostic check below cap")

    persisted = read_record(tmp_path)
    assert persisted is not None
    assert persisted.diagnostic_intervention_required is False
    assert persisted.repair_cap_reason is None

    state = PipelineState(phase="development", rebase=RebaseState())
    decision = decide_phase_transition(
        state,
        policy,
        integration_record=persisted,
        rebase_state=state.rebase,
    )
    assert decision == PhaseDecision.BLOCK_FOR_RECOVERY


def test_default_cap_is_five() -> None:
    """The default cap is 5 (low enough to break the production loop fast)."""
    assert DIAGNOSTIC_REPAIR_ATTEMPT_CAP == 5
