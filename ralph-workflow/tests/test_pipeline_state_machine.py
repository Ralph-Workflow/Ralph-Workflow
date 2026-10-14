"""Unit tests for the pipeline state machine (Authority 3)."""

from __future__ import annotations

from types import SimpleNamespace

from ralph.pipeline._pipeline_state_machine import (
    PhaseDecision,
    decide_phase_transition,
    is_terminal_success_eligible,
)
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.rebase_state import RebaseState


def _state(phase: str = "plan") -> SimpleNamespace:
    return SimpleNamespace(phase=phase, previous_phase=None)


def _policy_with(
    phases: dict[str, dict[str, str]], terminal: tuple[str, ...] = ()
) -> SimpleNamespace:
    phase_defs = {
        name: SimpleNamespace(
            transitions=SimpleNamespace(
                on_success=cfg.get("on_success"),
                on_failure=cfg.get("on_failure"),
                on_loopback=cfg.get("on_loopback"),
            ),
            role=cfg.get("role", "active"),
            terminal_outcome=cfg.get("terminal_outcome"),
        )
        for name, cfg in phases.items()
    }
    return SimpleNamespace(
        phases=phase_defs,
        terminal_states=lambda: set(terminal),
    )


def test_decide_returns_intervention_when_diagnostic_intervention_required() -> None:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        diagnostic_intervention_required=True,
    )
    policy = _policy_with({"plan": {"on_success": "build"}}, terminal=("done",))
    decision = decide_phase_transition(
        _state("plan"),
        policy,
        integration_record=record,
        rebase_state=RebaseState(),
    )
    assert decision is PhaseDecision.REPORT_INTERVENTION_REQUIRED


def test_decide_returns_block_for_recovery_when_integrating() -> None:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
    )
    policy = _policy_with({"plan": {"on_success": "build"}}, terminal=("done",))
    decision = decide_phase_transition(
        _state("plan"),
        policy,
        integration_record=record,
        rebase_state=RebaseState(),
    )
    assert decision is PhaseDecision.BLOCK_FOR_RECOVERY


def test_decide_returns_continue_phase_when_next_phase_matches_state() -> None:
    policy = _policy_with({"plan": {"on_success": "plan"}}, terminal=("done",))
    decision = decide_phase_transition(
        _state("plan"),
        policy,
        integration_record=None,
        rebase_state=RebaseState(),
    )
    assert decision is PhaseDecision.CONTINUE_PHASE


def test_decide_returns_advance_phase_when_next_phase_differs() -> None:
    policy = _policy_with(
        {"plan": {"on_success": "build"}, "build": {"on_success": "build"}},
        terminal=("done",),
    )
    decision = decide_phase_transition(
        _state("build"),
        policy,
        integration_record=None,
        rebase_state=RebaseState(),
    )
    assert decision is PhaseDecision.CONTINUE_PHASE


def test_decide_returns_terminal_success_when_next_phase_is_terminal() -> None:
    policy = _policy_with({"build": {"on_success": "done"}}, terminal=("done",))
    decision = decide_phase_transition(
        _state("build"),
        policy,
        integration_record=None,
        rebase_state=RebaseState(),
    )
    assert decision is PhaseDecision.REPORT_TERMINAL_SUCCESS


def test_decide_returns_block_for_recovery_when_integration_unresolved() -> None:
    """An unresolved rebase state blocks even when no record is present."""
    policy = _policy_with({"plan": {"on_success": "build"}}, terminal=("done",))
    decision = decide_phase_transition(
        _state("plan"),
        policy,
        integration_record=None,
        rebase_state=RebaseState(last_action="conflict"),
    )
    assert decision is PhaseDecision.BLOCK_FOR_RECOVERY


def test_is_terminal_success_eligible_returns_false_when_record_present() -> None:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
    )
    assert is_terminal_success_eligible(_state("done"), record, RebaseState()) is False


def test_is_terminal_success_eligible_returns_true_when_all_clear() -> None:
    rebase_state = RebaseState(
        last_publication_decision="publication_terminal",
        last_fan_out_decision="join_complete",
    )
    assert is_terminal_success_eligible(_state("done"), None, rebase_state) is True


def test_phase_decision_enum_has_six_values() -> None:
    assert len(PhaseDecision) == 6


def test_decide_returns_block_for_recovery_when_integrated_with_reintegrate_pending() -> None:
    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        integrated_feature_sha="c" * 40,
        reintegrate_pending=True,
    )
    policy = _policy_with({"plan": {"on_success": "build"}}, terminal=("done",))
    decision = decide_phase_transition(
        _state("plan"),
        policy,
        integration_record=record,
        rebase_state=RebaseState(),
    )
    assert decision is PhaseDecision.BLOCK_FOR_RECOVERY


def test_decide_durable_intervention_outranks_cached_block_for_recovery() -> None:
    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        integrated_feature_sha="c" * 40,
        diagnostic_intervention_required=True,
    )
    rebase_state = RebaseState(last_decision=PhaseDecision.BLOCK_FOR_RECOVERY.value)
    policy = _policy_with({"plan": {"on_success": "build"}}, terminal=("done",))
    decision = decide_phase_transition(
        _state("plan"),
        policy,
        integration_record=record,
        rebase_state=rebase_state,
    )
    assert decision is PhaseDecision.REPORT_INTERVENTION_REQUIRED


def test_persisted_block_does_not_block_when_no_authority_obligation() -> None:
    """A persisted ``BLOCK_FOR_RECOVERY`` decision must NOT block a fresh
    state with no current authority obligation.

    The blocking decision is a derived projection over CURRENT
    authority outputs, not a feedback input. The previous run
    loop turn's persisted decision is the *output* of the
    decision; re-using it as an *input* would let a settled
    obligation remain blocked because the previous turn's
    decision was blocking. With no current integration record
    and no current ``integration_unresolved`` flag, the decision
    must progress.
    """
    # No integration_record, no integration_unresolved, but a
    # persisted blocking decision from a previous turn. The
    # current state must NOT inherit the previous block.
    rebase_state = RebaseState(last_decision=PhaseDecision.BLOCK_FOR_RECOVERY.value)
    policy = _policy_with({"plan": {"on_success": "build"}}, terminal=("done",))
    decision = decide_phase_transition(
        _state("plan"),
        policy,
        integration_record=None,
        rebase_state=rebase_state,
    )
    assert decision is not PhaseDecision.BLOCK_FOR_RECOVERY
    assert decision is not PhaseDecision.REPORT_INTERVENTION_REQUIRED


def test_persisted_intervention_does_not_block_when_no_authority_obligation() -> None:
    """Same one-way-projection rule for the intervention decision."""
    rebase_state = RebaseState(last_decision=PhaseDecision.REPORT_INTERVENTION_REQUIRED.value)
    policy = _policy_with({"plan": {"on_success": "build"}}, terminal=("done",))
    decision = decide_phase_transition(
        _state("plan"),
        policy,
        integration_record=None,
        rebase_state=rebase_state,
    )
    assert decision is not PhaseDecision.REPORT_INTERVENTION_REQUIRED
    assert decision is not PhaseDecision.BLOCK_FOR_RECOVERY
