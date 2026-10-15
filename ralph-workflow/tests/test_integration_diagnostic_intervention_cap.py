"""Bounded-recovery tests for the diagnostic-repair attempt cap.

The production loop kept rotating inspection agents without ever
reaching a terminal state. The fix is a module-level cap; once
the cap is reached the diagnostic path emits RAISE_DIAGNOSTIC_INTERVENTION
and refuses to call any more agents.

Rewritten to drive the real recovery boundary:
(ralph.pipeline._integration_diagnostic.diagnose_retained_integration
and run_loop._block_unresolved_integration), asserting the durable
record's intervention flag and REPORT_INTERVENTION_REQUIRED decision.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from ralph.config.models import UnifiedConfig
from ralph.pipeline._integration_diagnostic import (
    DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
    diagnose_retained_integration,
)
from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
from ralph.pipeline.auto_integrate_record import (
    START_INTEGRATING,
    IntegrationRecord,
    read_record,
    write_record,
)
from ralph.pipeline.rebase_state import RebaseState
import ralph.pipeline.run_loop as run_loop
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from ralph.workspace.scope import WorkspaceScope


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


def _build_ctx(root: Path, policy: PipelinePolicy) -> MagicMock:
    bundle = load_policy(Path(__file__).parents[1] / "ralph" / "policy" / "defaults")
    bundle = bundle.model_copy(update={"pipeline": policy})
    empty_registry: dict[str, object] = {}
    ctx = MagicMock()
    ctx.workspace_scope = WorkspaceScope(root)
    ctx.config = UnifiedConfig()
    ctx.pipeline_deps = MagicMock()
    ctx.policy_bundle = bundle
    ctx.registry = empty_registry
    ctx.active_display = MagicMock()
    return ctx


def test_cap_emits_intervention_transition(tmp_path: Path) -> None:
    """Reaching the cap sets diagnostic_intervention_required and REPORT_INTERVENTION_REQUIRED."""
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
    write_record(tmp_path, record)

    # Drive recovery boundary
    result = diagnose_retained_integration(ctx, "completion receipt unreadable")
    assert result is False

    # Check on-disk record
    persisted = read_record(tmp_path)
    assert persisted is not None
    assert persisted.diagnostic_intervention_required is True
    assert persisted.merge_commit_repair_attempts == DIAGNOSTIC_REPAIR_ATTEMPT_CAP
    assert "diagnostic repair exhausted" in (persisted.repair_cap_reason or "")

    # Drive run-loop boundary
    state = PipelineState(phase="development", rebase=RebaseState())
    blocked = run_loop._block_unresolved_integration(state, ctx, "development")
    assert blocked is not None
    blocked_state = blocked[0]
    assert blocked_state.rebase.last_decision == PhaseDecision.REPORT_INTERVENTION_REQUIRED.value

    decision = decide_phase_transition(
        blocked_state,
        policy,
        integration_record=persisted,
        rebase_state=blocked_state.rebase,
    )
    assert decision == PhaseDecision.REPORT_INTERVENTION_REQUIRED


def test_cap_does_not_increment_attempt_counter(tmp_path: Path) -> None:
    """The over-cap handoff must not silently increment the counter."""
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
    assert persisted.merge_commit_repair_attempts == over_cap_count
    assert persisted.diagnostic_intervention_required is True

    state = PipelineState(phase="development", rebase=RebaseState())
    blocked = run_loop._block_unresolved_integration(state, ctx, "development")
    assert blocked is not None
    assert blocked[0].rebase.last_decision == PhaseDecision.REPORT_INTERVENTION_REQUIRED.value


def test_below_cap_preserves_intervention_false(tmp_path: Path) -> None:
    """A record below the cap retains intervention as False."""
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
