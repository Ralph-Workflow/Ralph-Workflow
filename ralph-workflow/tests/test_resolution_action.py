"""Tests for conflict-resolution ResolutionAction round selection."""

from __future__ import annotations

from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.attempt_fault import (
    INFRASTRUCTURE_TERMINATION_REASONS,
)
from ralph.pipeline.conflict_resolution.driver import (
    ResolutionAction,
    select_resolution_action,
)
from ralph.pipeline.conflict_resolution.session import ResolutionSession


def test_resolution_action_enum_values() -> None:
    """Verify that all ResolutionAction values exist as expected."""
    expected = {
        "CONTINUE_AGENT_ROUND",
        "ESCALATE_TO_INTERVENTION",
        "ACCEPT_WORKTREE_REPAIR",
        "PROCEED_TO_FAST_FORWARD",
    }
    assert {action.value for action in ResolutionAction} == expected


def test_select_resolution_action_continue_agent_round() -> None:
    """When unresolved paths remain and round cap is not reached, continue agent round."""
    session = ResolutionSession(
        unresolved_paths=("conflict.py",),
        terminal_reason=None,
    )
    action = select_resolution_action(session, round_index=1, cap=3)
    assert action is ResolutionAction.CONTINUE_AGENT_ROUND


def test_select_resolution_action_does_not_escalate_on_round_cap() -> None:
    """Action selection does not independently cap rounds; ledger and operator caps decide."""
    session = ResolutionSession(
        unresolved_paths=("conflict.py",),
        terminal_reason=None,
    )
    action = select_resolution_action(session, round_index=3, cap=3)
    assert action is ResolutionAction.CONTINUE_AGENT_ROUND


def test_select_resolution_action_escalate_to_intervention_out_of_reach() -> None:
    """When out_of_reach_paths exist, escalate to intervention regardless of round cap."""
    session = ResolutionSession(
        unresolved_paths=("submodule",),
        out_of_reach_paths=("submodule",),
        terminal_reason=None,
    )
    action = select_resolution_action(session, round_index=1, cap=5)
    assert action is ResolutionAction.ESCALATE_TO_INTERVENTION


def test_select_resolution_action_accept_worktree_repair() -> None:
    """When worktree markers are resolved despite agent fault, accept worktree repair."""
    session = ResolutionSession(
        unresolved_paths=(),
        terminal_reason=ResolutionTerminationReason.CANDIDATE_EXITED,
    )
    action = select_resolution_action(session, round_index=1, cap=3)
    assert action is ResolutionAction.ACCEPT_WORKTREE_REPAIR


def test_select_resolution_action_proceed_to_fast_forward() -> None:
    """When resolution succeeded cleanly with no unresolved paths, proceed to fast forward."""
    session = ResolutionSession(
        unresolved_paths=(),
        terminal_reason=None,
    )
    action = select_resolution_action(session, round_index=1, cap=3)
    assert action is ResolutionAction.PROCEED_TO_FAST_FORWARD


def test_infrastructure_termination_reason_transport_loop_detected() -> None:
    """TRANSPORT_LOOP_DETECTED escalates to intervention."""
    session = ResolutionSession(
        unresolved_paths=("conflict.py",),
        terminal_reason=ResolutionTerminationReason.TRANSPORT_LOOP_DETECTED,
    )
    action = select_resolution_action(session, round_index=1, cap=5)
    assert action is ResolutionAction.ESCALATE_TO_INTERVENTION


def test_infrastructure_termination_reason_supervision_infrastructure_failure() -> None:
    """SUPERVISION_INFRASTRUCTURE_FAILURE escalates to intervention."""
    session = ResolutionSession(
        unresolved_paths=("conflict.py",),
        terminal_reason=ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE,
    )
    action = select_resolution_action(session, round_index=1, cap=5)
    assert action is ResolutionAction.ESCALATE_TO_INTERVENTION


def test_infrastructure_termination_reason_tool_surface_dead() -> None:
    """TOOL_SURFACE_DEAD escalates to intervention."""
    session = ResolutionSession(
        unresolved_paths=("conflict.py",),
        terminal_reason=ResolutionTerminationReason.TOOL_SURFACE_DEAD,
    )
    action = select_resolution_action(session, round_index=1, cap=5)
    assert action is ResolutionAction.ESCALATE_TO_INTERVENTION


def test_infrastructure_termination_reason_candidate_unavailable() -> None:
    """CANDIDATE_UNAVAILABLE escalates to intervention."""
    session = ResolutionSession(
        unresolved_paths=("conflict.py",),
        terminal_reason=ResolutionTerminationReason.CANDIDATE_UNAVAILABLE,
    )
    action = select_resolution_action(session, round_index=1, cap=5)
    assert action is ResolutionAction.ESCALATE_TO_INTERVENTION


def test_all_infrastructure_termination_reasons_covered() -> None:
    """Ensure every reason in INFRASTRUCTURE_TERMINATION_REASONS is tested."""
    expected_reasons = {
        ResolutionTerminationReason.TRANSPORT_LOOP_DETECTED,
        ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE,
        ResolutionTerminationReason.TOOL_SURFACE_DEAD,
        ResolutionTerminationReason.CANDIDATE_UNAVAILABLE,
    }
    assert set(INFRASTRUCTURE_TERMINATION_REASONS) == expected_reasons
    for reason in INFRASTRUCTURE_TERMINATION_REASONS:
        session = ResolutionSession(
            unresolved_paths=("conflict.py",),
            terminal_reason=reason,
        )
        assert (
            select_resolution_action(session, round_index=1, cap=5)
            is ResolutionAction.ESCALATE_TO_INTERVENTION
        )
