from __future__ import annotations

from ralph.display.auto_integrate_message import OperationDisplayState
from ralph.pipeline.auto_integrate_boundary import record_already_landed
from ralph.pipeline.integration_resolution import RebaseOutcomeKind
from ralph.pipeline.rebase_state import RebaseState


def _rebased_not_landed() -> RebaseState:
    return RebaseState(last_outcome=RebaseOutcomeKind.REBASED, last_target="main")


def test_work_rebased_but_not_yet_landed_is_landing() -> None:
    assert _rebased_not_landed().operation_display_state == "landing"


def test_work_that_has_landed_is_no_longer_shown_as_landing() -> None:
    landed = _rebased_not_landed().model_copy(
        update={"last_outcome": RebaseOutcomeKind.SKIPPED, "fast_forwarded": True}
    )

    assert landed.operation_display_state != "landing"


def test_the_label_follows_the_record_when_its_facts_change() -> None:
    assert record_already_landed(_rebased_not_landed()).operation_display_state != "landing"


def test_an_explicit_state_holds_until_the_facts_it_describes_change() -> None:
    waiting = _rebased_not_landed().set_operation_display_state(OperationDisplayState.WAITING)

    assert waiting.model_copy(update={"last_reason": "still waiting"}).operation_display_state == (
        "waiting"
    )
    moved_on = waiting.model_copy(
        update={"last_outcome": RebaseOutcomeKind.SKIPPED, "fast_forwarded": True}
    )
    assert moved_on.operation_display_state != "waiting"
