import ralph.pipeline._integration_diagnostic as diagnostic
from ralph.pipeline._integration_action import IntegrationAction
from ralph.pipeline.rebase_state import RebaseOutcomeKind, RebaseState


def test_factory_returns_conflict_scratch_state() -> None:
    assert diagnostic._busy_refusal_state(1234.5) == RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        retry_deadline=1234.5,
    )


def test_factory_passthrough_none() -> None:
    assert diagnostic._busy_refusal_state(None) == RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        retry_deadline=None,
    )


def test_migrated_call_sites_use_factory_and_do_not_persist(monkeypatch) -> None:
    import ralph.pipeline._integration_diagnostic as diagnostic
    import ralph.pipeline.auto_integrate_recovery as recovery

    produced: list[RebaseState] = []
    consumed: list[RebaseState] = []
    real_factory = diagnostic._busy_refusal_state
    real_action = diagnostic.next_integration_action

    def factory(retry_deadline: float | None) -> RebaseState:
        state = real_factory(retry_deadline)
        produced.append(state)
        return state

    def capture(state: RebaseState, *, clock: object = None) -> IntegrationAction:
        consumed.append(state)
        return real_action(state, clock=clock)

    def persist(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("busy-refusal scratch state was persisted")

    monkeypatch.setattr(diagnostic, "_busy_refusal_state", factory)
    monkeypatch.setattr(recovery, "_busy_refusal_state", factory)
    monkeypatch.setattr(diagnostic, "next_integration_action", capture)
    monkeypatch.setattr(recovery, "next_integration_action", capture)
    for name in ("write_record", "write_text_if_changed", "apply_record_transition"):
        if hasattr(diagnostic, name):
            monkeypatch.setattr(diagnostic, name, persist)
    for name in ("apply_record_transition", "_clear_record"):
        monkeypatch.setattr(recovery, name, persist)

    for deadline in (None, 999.0):
        produced.clear()
        consumed.clear()
        diagnostic_action = diagnostic.busy_refusal_outcome(deadline)
        recovery_action = recovery.busy_refusal_outcome(deadline)
        assert isinstance(diagnostic_action, IntegrationAction)
        assert isinstance(recovery_action, IntegrationAction)
        assert diagnostic_action == recovery_action
        assert len(produced) == 2
        assert consumed[0] is produced[0]
        assert consumed[1] is produced[1]
        assert produced[0] == produced[1]
        assert produced[0].last_outcome == RebaseOutcomeKind.CONFLICT
        assert produced[0].retry_deadline == deadline
