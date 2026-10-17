"""Tests for RebaseOutcomeKind enum, round-trip mapping, and terminal predicate."""

import pytest

from ralph.display.auto_integrate_message import (
    OperationDisplayState,
    rebase_outcome_to_operation_display_state,
)
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline.rebase_state import RebaseState

ALL_19_LEGACY_VERBS = [
    "rebased",
    "merged",
    "fast_forwarded",
    "skipped",
    "conflict",
    "recovered",
    "diagnosing",
    "evidence_diagnosis",
    "intervention_required",
    "diagnostic_intervention_required",
    "terminal",
    "completed",
    "remote_pushed",
    "remote_failed",
    "integrating",
    "landing",
    "publishing",
    "conflict_resolution",
    "waiting",
]


@pytest.mark.parametrize("verb", ALL_19_LEGACY_VERBS)
def test_rebase_outcome_kind_round_trip(verb: str) -> None:
    """Each of the 19 live legacy verbs must round-trip through RebaseOutcomeKind."""
    kind = RebaseOutcomeKind.from_legacy(verb)
    assert kind is not None
    assert kind.value == verb
    assert RebaseOutcomeKind.from_legacy(kind.value) == kind


def test_rebase_outcome_kind_from_legacy_none() -> None:
    """None maps to None."""
    assert RebaseOutcomeKind.from_legacy(None) is None


def test_rebase_outcome_kind_from_legacy_alias() -> None:
    """fast_forward maps to FAST_FORWARDED."""
    assert RebaseOutcomeKind.from_legacy("fast_forward") == RebaseOutcomeKind.FAST_FORWARDED


def test_rebase_outcome_kind_is_terminal() -> None:
    """is_terminal returns True only for terminal, completed, and remote_pushed."""
    terminal_kinds = {
        RebaseOutcomeKind.TERMINAL,
        RebaseOutcomeKind.COMPLETED,
        RebaseOutcomeKind.REMOTE_PUSHED,
    }
    for kind in RebaseOutcomeKind:
        if kind in terminal_kinds:
            assert kind.is_terminal() is True
        else:
            assert kind.is_terminal() is False


def test_rebase_state_last_action_projection() -> None:
    """RebaseState.last_action projects the string value of last_outcome."""
    state = RebaseState(last_outcome=RebaseOutcomeKind.REBASED)
    assert state.last_action == "rebased"

    state_none = RebaseState(last_outcome=None)
    assert state_none.last_action is None

    state_failed = RebaseState(last_outcome=RebaseOutcomeKind.REMOTE_FAILED)
    assert state_failed.last_action == "remote_failed"


def test_rebase_state_from_legacy_payload_migration(caplog: pytest.LogCaptureFixture) -> None:
    """Legacy checkpoints deserialize and migrate via from_legacy_payload with warning."""
    for verb in ALL_19_LEGACY_VERBS:
        payload = {"last_action": verb, "fast_forwarded": False}
        state = RebaseState.from_legacy_payload(payload)
        expected_kind = RebaseOutcomeKind.from_legacy(verb)
        assert state.last_outcome == expected_kind
        assert state.last_action == verb

    # None case
    state_none = RebaseState.from_legacy_payload({"last_action": None})
    assert state_none.last_outcome is None
    assert state_none.last_action is None


def test_rebase_state_coerce_legacy_init() -> None:
    """Instantiating RebaseState with legacy last_action keyword argument coerces cleanly."""
    state = RebaseState(last_action="conflict")
    assert state.last_outcome == RebaseOutcomeKind.CONFLICT
    assert state.last_action == "conflict"


@pytest.mark.parametrize("verb", ALL_19_LEGACY_VERBS)
def test_rebase_outcome_kind_to_operation_display_state(verb: str) -> None:
    """Each of the 19 RebaseOutcomeKind values projects to an OperationDisplayState."""
    kind = RebaseOutcomeKind.from_legacy(verb)
    assert kind is not None
    display_state = rebase_outcome_to_operation_display_state(kind)
    assert display_state is not None
    assert isinstance(display_state, OperationDisplayState)
    assert display_state in OperationDisplayState


def test_rebase_outcome_to_operation_display_state_none() -> None:
    """None outcome projects to None."""
    assert rebase_outcome_to_operation_display_state(None) is None


def test_rebase_state_last_outcome_to_operation_display_state() -> None:
    """RebaseState.last_outcome projects to OperationDisplayState through rebase_outcome_to_operation_display_state."""
    state = RebaseState(last_outcome=RebaseOutcomeKind.REBASED)
    assert (
        rebase_outcome_to_operation_display_state(state.last_outcome)
        == OperationDisplayState.LANDING
    )

    state_conflict = RebaseState(last_outcome=RebaseOutcomeKind.CONFLICT)
    assert (
        rebase_outcome_to_operation_display_state(state_conflict.last_outcome)
        == OperationDisplayState.CONFLICT_RESOLUTION
    )

    state_diagnosing = RebaseState(last_outcome=RebaseOutcomeKind.DIAGNOSING)
    assert (
        rebase_outcome_to_operation_display_state(state_diagnosing.last_outcome)
        == OperationDisplayState.EVIDENCE_DIAGNOSIS
    )

    state_remote_pushed = RebaseState(last_outcome=RebaseOutcomeKind.REMOTE_PUSHED)
    assert (
        rebase_outcome_to_operation_display_state(state_remote_pushed.last_outcome)
        == OperationDisplayState.TERMINAL
    )

    state_remote_failed = RebaseState(last_outcome=RebaseOutcomeKind.REMOTE_FAILED)
    assert (
        rebase_outcome_to_operation_display_state(state_remote_failed.last_outcome)
        == OperationDisplayState.PUBLISHING
    )


def test_rebase_outcome_kind_from_legacy_unknown() -> None:
    """Unknown legacy strings map to RebaseOutcomeKind.UNKNOWN."""
    assert RebaseOutcomeKind.from_legacy("unrecognized_bogus") == RebaseOutcomeKind.UNKNOWN
    assert RebaseOutcomeKind.from_legacy("unknown") == RebaseOutcomeKind.UNKNOWN


def test_rebase_outcome_kind_unknown_not_terminal() -> None:
    """UNKNOWN outcome kind is not terminal."""
    assert RebaseOutcomeKind.UNKNOWN.is_terminal() is False


def test_rebase_state_coerce_legacy_action_with_none_outcome() -> None:
    """last_action coerces to last_outcome when last_outcome is explicitly None."""
    state = RebaseState.model_validate({"last_action": "conflict", "last_outcome": None})
    assert state.last_outcome == RebaseOutcomeKind.CONFLICT
    assert state.last_action == "conflict"
    assert state.integration_unresolved is True


def test_rebase_state_unknown_legacy_action() -> None:
    """Unknown legacy action produces UNKNOWN and marks integration unresolved and intervention required."""
    state = RebaseState(last_action="unrecognized_bogus")
    assert state.last_outcome == RebaseOutcomeKind.UNKNOWN
    assert state.last_action == "unknown"
    assert state.integration_unresolved is True
    assert state.is_intervention_required is True


def test_rebase_state_contradictory_values() -> None:
    """Contradictory last_action and last_outcome fail closed to UNKNOWN."""
    state = RebaseState.model_validate({"last_action": "conflict", "last_outcome": "rebased"})
    assert state.last_outcome == RebaseOutcomeKind.UNKNOWN
    assert state.last_action == "unknown"
    assert state.integration_unresolved is True
    assert state.is_intervention_required is True


def test_rebase_state_matching_action_and_outcome() -> None:
    """Consistent last_action and last_outcome resolve to the agreed outcome."""
    state1 = RebaseState.model_validate({"last_action": "conflict", "last_outcome": "conflict"})
    assert state1.last_outcome == RebaseOutcomeKind.CONFLICT

    state2 = RebaseState.model_validate(
        {"last_action": "rebased", "last_outcome": RebaseOutcomeKind.REBASED}
    )
    assert state2.last_outcome == RebaseOutcomeKind.REBASED


def test_rebase_state_typed_predicates() -> None:
    """RebaseState typed predicates route accurately through last_outcome."""
    rebased = RebaseState(last_outcome=RebaseOutcomeKind.REBASED, fast_forwarded=False)
    assert rebased.is_rebasing_or_merging() is True
    assert rebased.is_landing() is True
    assert rebased.is_skipped() is False

    merged = RebaseState(last_outcome=RebaseOutcomeKind.MERGED, fast_forwarded=True)
    assert merged.is_rebasing_or_merging() is True
    assert merged.is_landing() is True
    assert merged.is_skipped() is False

    skipped = RebaseState(last_outcome=RebaseOutcomeKind.SKIPPED)
    assert skipped.is_skipped() is True
    assert skipped.is_rebasing_or_merging() is False

    conflict = RebaseState(last_outcome=RebaseOutcomeKind.CONFLICT)
    assert conflict.is_conflict_resolution() is True
    assert conflict.is_skipped() is False

    diag = RebaseState(last_outcome=RebaseOutcomeKind.DIAGNOSING)
    assert diag.is_diagnostic_action() is True


def test_remote_sync_status_enum() -> None:
    """RemoteSyncStatus enum members match legacy strings and round-trip."""
    from ralph.pipeline.auto_integrate_remote_sync import (
        REMOTE_NO_REMOTE,
        REMOTE_PUSHED,
        RemoteSyncStatus,
    )

    assert RemoteSyncStatus.NO_REMOTE.value == "no remote"
    assert REMOTE_NO_REMOTE == RemoteSyncStatus.NO_REMOTE
    assert RemoteSyncStatus.PUSHED.value == "pushed"
    assert REMOTE_PUSHED == RemoteSyncStatus.PUSHED
    assert isinstance(RemoteSyncStatus.NO_REMOTE, str)


def test_set_fan_out_decision_round_trip() -> None:
    """Every FanOutDecision member is accepted and round-trips to .value."""
    from ralph.pipeline._fan_out_authority import FanOutDecision

    state = RebaseState()
    for decision in FanOutDecision:
        updated = state.set_fan_out_decision(decision)
        assert updated.last_fan_out_decision == decision.value
        assert updated.last_fan_out_decision == str(decision)
        # frozen model: the original snapshot is untouched
        assert state.last_fan_out_decision is None


def test_set_fan_out_decision_rejects_invalid() -> None:
    """Strings, foreign enums, and None are rejected with ValueError."""
    from enum import StrEnum

    from ralph.pipeline._fan_out_authority import FanOutDecision

    class Foreign(StrEnum):
        JOIN_PENDING = "join_pending"

    state = RebaseState()
    setter = state.set_fan_out_decision
    for bad in ("join_pending", "bogus", Foreign.JOIN_PENDING, None):
        with pytest.raises(ValueError, match="invalid fan_out_decision"):
            setter(bad)
    assert FanOutDecision("join_pending")  # sanity: valid vocab stays usable


LEGACY_OPERATION_DISPLAY_ALIASES = [
    "integration",
    "publication",
    "intervention",
    "completed",
    "diagnosing",
]


def test_set_operation_display_state_round_trip() -> None:
    """Every OperationDisplayState member is accepted and round-trips to .value."""
    state = RebaseState()
    for display in OperationDisplayState:
        updated = state.set_operation_display_state(display)
        assert updated.operation_display_state == display.value
        # frozen model: the original snapshot is untouched
        assert state.operation_display_state is None


def test_set_operation_display_state_rejects_invalid_and_legacy() -> None:
    """Strings (including legacy aliases), foreign enums, None rejected."""
    from enum import StrEnum

    class Foreign(StrEnum):
        WAITING = "waiting"

    state = RebaseState()
    setter = state.set_operation_display_state
    for bad in ("waiting", "bogus", *LEGACY_OPERATION_DISPLAY_ALIASES, Foreign.WAITING, None):
        with pytest.raises(ValueError, match="invalid operation_display_state"):
            setter(bad)
