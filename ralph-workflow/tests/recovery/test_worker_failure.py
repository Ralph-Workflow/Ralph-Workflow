from __future__ import annotations

from ralph.pipeline.events import WorkerFailedEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState, WorkerStatus
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FailureEvent, FailureEventBus

_MIN_ERROR_LEN = 10


def _make_work_unit(unit_id: str) -> WorkUnit:
    return WorkUnit(unit_id=unit_id, description="Test unit", allowed_directories=[])


def _make_state_with_workers(unit_ids: list[str]) -> PipelineState:
    work_units = tuple(_make_work_unit(uid) for uid in unit_ids)
    worker_states = {uid: WorkerState(unit_id=uid, status=WorkerStatus.RUNNING) for uid in unit_ids}
    return PipelineState(
        phase="development",
        work_units=work_units,
        worker_states=worker_states,
    )


def _make_state_with_workers_and_chain(unit_ids: list[str], agents: list[str]) -> PipelineState:
    work_units = tuple(_make_work_unit(uid) for uid in unit_ids)
    worker_states = {uid: WorkerState(unit_id=uid, status=WorkerStatus.RUNNING) for uid in unit_ids}
    return PipelineState(
        phase="development",
        phase_chains={"development": AgentChainState(agents=agents, current_index=0, retries=0)},
        work_units=work_units,
        worker_states=worker_states,
    )


def test_single_worker_failure_sets_failed_status() -> None:
    state = _make_state_with_workers(["w1", "w2"])

    event = WorkerFailedEvent(unit_id="w1", exit_code=1, error="agent crashed")
    new_state, effects = reduce(state, event, None)

    assert new_state.worker_states["w1"].status == WorkerStatus.FAILED
    assert new_state.worker_states["w2"].status == WorkerStatus.RUNNING
    assert new_state.phase == "development"
    assert effects == []


def test_worker_failure_preserves_work_units() -> None:
    state = _make_state_with_workers(["w1"])
    event = WorkerFailedEvent(unit_id="w1", exit_code=2, error="timeout")
    new_state, _ = reduce(state, event, None)

    assert len(new_state.work_units) == 1
    assert new_state.work_units[0].unit_id == "w1"


def test_worker_failure_routes_through_recovery_controller() -> None:
    bus = FailureEventBus()
    collected: list[FailureEvent] = []
    bus.subscribe(lambda evt: collected.append(evt) if isinstance(evt, FailureEvent) else None)

    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10, event_bus=bus))

    state = _make_state_with_workers_and_chain(["w1", "w2"], agents=["claude"])

    event = WorkerFailedEvent(unit_id="w1", exit_code=1, error="connection reset by peer")
    new_state, _ = reduce(state, event, None, recovery=controller)

    assert new_state.worker_states["w1"].status == WorkerStatus.FAILED
    assert new_state.worker_states["w2"].status == WorkerStatus.RUNNING
    assert new_state.phase == "development"
    assert len(collected) == 1
    assert collected[0].category == "environmental"
    assert collected[0].counted_against_budget is False


def test_worker_failure_with_agent_timeout_routes_through_recovery() -> None:
    bus = FailureEventBus()
    collected: list[FailureEvent] = []
    bus.subscribe(lambda evt: collected.append(evt) if isinstance(evt, FailureEvent) else None)

    controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10, event_bus=bus))

    state = _make_state_with_workers_and_chain(["w1"], agents=["claude"])

    event = WorkerFailedEvent(
        unit_id="w1",
        exit_code=1,
        error="AgentInactivityTimeoutError: agent idle",
    )
    new_state, _ = reduce(state, event, None, recovery=controller)

    assert new_state.worker_states["w1"].status == WorkerStatus.FAILED
    assert len(collected) == 1


def test_worker_failure_legacy_path_without_recovery_controller() -> None:
    state = _make_state_with_workers(["w1"])

    event = WorkerFailedEvent(unit_id="w1", exit_code=1, error="agent crashed")
    new_state, effects = reduce(state, event, None, recovery=None)

    assert new_state.worker_states["w1"].status == WorkerStatus.FAILED
    assert new_state.phase == "development"
    assert effects == []
