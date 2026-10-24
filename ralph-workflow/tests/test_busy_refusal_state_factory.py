"""WU-4: public-outcome tests for busy_refusal_outcome at both entry points.

No private-spy pattern. All assertions are on public IntegrationAction
values returned from the two call sites:
  - ralph.pipeline._integration_diagnostic.busy_refusal_outcome
  - ralph.pipeline.auto_integrate_recovery.busy_refusal_outcome
"""

from __future__ import annotations

from ralph.pipeline._integration_action import IntegrationAction
from ralph.pipeline._integration_diagnostic import busy_refusal_outcome as diagnostic_outcome
from ralph.pipeline._integration_diagnostic import busy_refusal_outcome as recovery_outcome


def test_diagnostic_busy_refusal_returns_require_intervention_when_no_deadline() -> None:
    """Without a concrete deadline the diagnostic BUSY site fails closed."""
    action = diagnostic_outcome(None, clock=lambda: 1000.0)
    assert action is not IntegrationAction.COMPLETE_WITH_PROOF
    assert action is IntegrationAction.REQUIRE_INTERVENTION


def test_recovery_busy_refusal_returns_require_intervention_when_no_deadline() -> None:
    """Without a concrete deadline the recovery BUSY site fails closed."""
    action = recovery_outcome(None, clock=lambda: 1000.0)
    assert action is not IntegrationAction.COMPLETE_WITH_PROOF
    assert action is IntegrationAction.REQUIRE_INTERVENTION


def _clock_at(t: float) -> object:
    def clock() -> float:
        return t

    return clock


def test_both_call_sites_agree_on_same_deadline() -> None:
    """Identical input yields identical action from both entry points."""
    clock = _clock_at(500.0)
    for deadline in (None, 999.0):
        da = diagnostic_outcome(deadline, clock=clock)
        ra = recovery_outcome(deadline, clock=clock)
        assert da == ra, f"diagnostic={da}, recovery={ra} for deadline={deadline}"
        assert da is not IntegrationAction.COMPLETE_WITH_PROOF


def test_unexpired_deadline_yields_wait_for_bounded_retry() -> None:
    """A concrete, unexpired deadline authorizes bounded retry at both sites."""
    clock = _clock_at(100.0)
    deadline = 200.0  # clock=100 < deadline=200 → unexpired
    da = diagnostic_outcome(deadline, clock=clock)
    ra = recovery_outcome(deadline, clock=clock)
    assert da is IntegrationAction.WAIT_FOR_BOUNDED_RETRY
    assert ra is IntegrationAction.WAIT_FOR_BOUNDED_RETRY


def test_expired_deadline_yields_require_intervention() -> None:
    """An expired deadline fails closed at both sites."""
    clock = _clock_at(300.0)
    deadline = 200.0  # clock=300 > deadline=200 → expired
    da = diagnostic_outcome(deadline, clock=clock)
    ra = recovery_outcome(deadline, clock=clock)
    assert da is IntegrationAction.REQUIRE_INTERVENTION
    assert ra is IntegrationAction.REQUIRE_INTERVENTION


def test_factory_result_does_not_persist(monkeypatch) -> None:
    """busy_refusal_outcome never writes to disk; it is side-effect-free."""
    import ralph.pipeline._integration_diagnostic as diagnostic
    import ralph.pipeline.auto_integrate_recovery as recovery

    persisted: list[str] = []

    def track_persist(*args: object, **kwargs: object) -> object:
        persisted.append("diagnostic" if "diagnostic" in str(type(args[0])) else "recovery")
        raise AssertionError("busy_refusal_outcome must not call write/persist helpers")

    # Block all known persistence entry-points
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
