from __future__ import annotations

from ralph.pipeline._integration_action import IntegrationAction
from ralph.pipeline._integration_diagnostic import busy_refusal_outcome as diagnostic_outcome
from ralph.pipeline._integration_diagnostic import busy_refusal_outcome as recovery_outcome
from ralph.pipeline.integration_decision import CleanupLadderRung
from ralph.pipeline.integration_owner import (
    RecordFactOutcome,
    ladder_rung_for_record_fact_outcome,
)


def test_diagnostic_busy_refusal_fails_closed_to_stale_intent_when_no_deadline() -> None:
    action = diagnostic_outcome(None, clock=lambda: 1000.0)
    assert action is not IntegrationAction.COMPLETE_WITH_PROOF
    assert action is RecordFactOutcome.STALE_INTENT
    assert ladder_rung_for_record_fact_outcome(action) is CleanupLadderRung.NORMAL_CLEANUP


def test_recovery_busy_refusal_fails_closed_to_stale_intent_when_no_deadline() -> None:
    action = recovery_outcome(None, clock=lambda: 1000.0)
    assert action is not IntegrationAction.COMPLETE_WITH_PROOF
    assert action is RecordFactOutcome.STALE_INTENT
    assert ladder_rung_for_record_fact_outcome(action) is CleanupLadderRung.NORMAL_CLEANUP


def _clock_at(t: float) -> object:
    def clock() -> float:
        return t

    return clock


def test_both_call_sites_agree_on_same_deadline() -> None:
    clock = _clock_at(500.0)
    for deadline in (None, 999.0):
        da = diagnostic_outcome(deadline, clock=clock)
        ra = recovery_outcome(deadline, clock=clock)
        assert da == ra, f"diagnostic={da}, recovery={ra} for deadline={deadline}"
        assert da is not IntegrationAction.COMPLETE_WITH_PROOF


def test_unexpired_deadline_yields_wait_for_bounded_retry() -> None:
    clock = _clock_at(100.0)
    deadline = 200.0
    da = diagnostic_outcome(deadline, clock=clock)
    ra = recovery_outcome(deadline, clock=clock)
    assert da is IntegrationAction.WAIT_FOR_BOUNDED_RETRY
    assert ra is IntegrationAction.WAIT_FOR_BOUNDED_RETRY


def test_expired_deadline_fails_closed_to_stale_intent() -> None:
    clock = _clock_at(300.0)
    deadline = 200.0
    da = diagnostic_outcome(deadline, clock=clock)
    ra = recovery_outcome(deadline, clock=clock)
    assert da is RecordFactOutcome.STALE_INTENT
    assert ra is RecordFactOutcome.STALE_INTENT
    assert ladder_rung_for_record_fact_outcome(da) is CleanupLadderRung.NORMAL_CLEANUP
    assert ladder_rung_for_record_fact_outcome(ra) is CleanupLadderRung.NORMAL_CLEANUP


def test_factory_result_does_not_persist(monkeypatch) -> None:
    import ralph.pipeline._integration_diagnostic as diagnostic
    import ralph.pipeline.auto_integrate_recovery as recovery

    persisted: list[str] = []

    def track_persist(*args: object, **kwargs: object) -> object:
        persisted.append("diagnostic" if "diagnostic" in str(type(args[0])) else "recovery")
        raise AssertionError("busy_refusal_outcome must not call write/persist helpers")

    for name in ("write_record", "write_text_if_changed", "apply_record_transition"):
        if hasattr(diagnostic, name):
            monkeypatch.setattr(diagnostic, name, track_persist)
    for name in ("apply_record_transition", "_clear_record"):
        monkeypatch.setattr(recovery, name, track_persist)

    for deadline in (None, 999.0):
        persisted.clear()
        diagnostic_outcome(deadline, clock=lambda: 100.0)
        recovery_outcome(deadline, clock=lambda: 100.0)
        assert persisted == [], f"unexpected persist calls for deadline={deadline}: {persisted}"
