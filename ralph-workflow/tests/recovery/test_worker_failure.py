"""Black-box test: worker failures do not terminate the pipeline."""

from __future__ import annotations

from ralph.pipeline.events import WorkerFailedEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState, WorkerStatus
from ralph.recovery.budget import AgentBudgetRegistry
from ralph.recovery.controller import RecoveryController
from ralph.recovery.events import FailureEvent, FailureEventBus

_MIN_ERROR_LEN = 10


def _make_work_unit(unit_id: str) -> WorkUnit:
    return WorkUnit(unit_id=unit_id, description="Test unit", allowed_directories=[])


def _make_state_with_workers(unit_ids: list[str]) -> PipelineState:
    work_units = tuple(_make_work_unit(uid) for uid in unit_ids)
    worker_states = {
        uid: WorkerState(unit_id=uid, status=WorkerStatus.RUNNING) for uid in unit_ids
    }
    return PipelineState(
        phase="development",
        work_units=work_units,
        worker_states=worker_states,
    )


def _make_state_with_workers_and_chain(unit_ids: list[str], agents: list[str]) -> PipelineState:
    """Create state with workers and an agent chain for RecoveryController."""
    work_units = tuple(_make_work_unit(uid) for uid in unit_ids)
    worker_states = {
        uid: WorkerState(unit_id=uid, status=WorkerStatus.RUNNING) for uid in unit_ids
    }
    return PipelineState(
        phase="development",
        dev_chain=AgentChainState(agents=agents, current_index=0, retries=0),
        work_units=work_units,
        worker_states=worker_states,
    )


def test_single_worker_failure_sets_failed_status() -> None:
    """A single WorkerFailedEvent marks that worker FAILED but does not terminate pipeline."""
    state = _make_state_with_workers(["w1", "w2"])

    event = WorkerFailedEvent(unit_id="w1", exit_code=1, error="agent crashed")
    new_state, effects = reduce(state, event, None)

    # w1 is now FAILED; w2 still RUNNING
    assert new_state.worker_states["w1"].status == WorkerStatus.FAILED
    assert new_state.worker_states["w2"].status == WorkerStatus.RUNNING
    # Pipeline phase unchanged — worker failures don't terminate
    assert new_state.phase == "development"
    assert effects == []


def test_worker_failure_preserves_work_units() -> None:
    """Worker failure must not destroy the work_units tuple on the state."""
    state = _make_state_with_workers(["w1"])
    event = WorkerFailedEvent(unit_id="w1", exit_code=2, error="timeout")
    new_state, _ = reduce(state, event, None)

    assert len(new_state.work_units) == 1
    assert new_state.work_units[0].unit_id == "w1"


def test_worker_failure_routes_through_recovery_controller() -> None:
    """WorkerFailedEvent routes through RecoveryController when provided.

    This ensures worker failures are attributed and can trigger retry/fallover
    decisions at the phase layer, rather than being handled in isolation.
    """
    bus = FailureEventBus()
    collected: list[FailureEvent] = []
    bus.subscribe(lambda evt: collected.append(evt) if isinstance(evt, FailureEvent) else None)

    registry = AgentBudgetRegistry().set_budget(
        "development", "claude", max_retries=3
    )
    controller = RecoveryController(cycle_cap=10, event_bus=bus, budget_registry=registry)

    state = _make_state_with_workers_and_chain(["w1", "w2"], agents=["claude"])

    # Environmental error - should not change phase
    event = WorkerFailedEvent(unit_id="w1", exit_code=1, error="connection reset by peer")
    new_state, _ = reduce(state, event, None, recovery=controller)

    # Worker is marked FAILED
    assert new_state.worker_states["w1"].status == WorkerStatus.FAILED
    assert new_state.worker_states["w2"].status == WorkerStatus.RUNNING
    # Phase unchanged because environmental failures don't trigger phase transition
    assert new_state.phase == "development"
    # Failure event was published to the bus
    assert len(collected) == 1
    assert collected[0].category == "environmental"
    assert collected[0].counted_against_budget is False


def test_worker_failure_with_agent_timeout_routes_through_recovery() -> None:
    """WorkerFailedEvent with agent-style error routes through RecoveryController."""
    bus = FailureEventBus()
    collected: list[FailureEvent] = []
    bus.subscribe(lambda evt: collected.append(evt) if isinstance(evt, FailureEvent) else None)

    registry = AgentBudgetRegistry().set_budget(
        "development", "claude", max_retries=3
    )
    controller = RecoveryController(cycle_cap=10, event_bus=bus, budget_registry=registry)

    state = _make_state_with_workers_and_chain(["w1"], agents=["claude"])

    # Agent-style error - would be classified as AGENT if it matched the timeout pattern
    event = WorkerFailedEvent(
        unit_id="w1",
        exit_code=1,
        error="AgentInactivityTimeoutError: agent idle",
    )
    new_state, _ = reduce(state, event, None, recovery=controller)

    # Worker is marked FAILED
    assert new_state.worker_states["w1"].status == WorkerStatus.FAILED
    # Failure event was published
    assert len(collected) == 1


def test_worker_failure_legacy_path_without_recovery_controller() -> None:
    """WorkerFailedEvent uses legacy path when no RecoveryController is provided.

    This preserves backward compatibility for tests and contexts that don't
    use the RecoveryController.
    """
    state = _make_state_with_workers(["w1"])

    event = WorkerFailedEvent(unit_id="w1", exit_code=1, error="agent crashed")
    new_state, effects = reduce(state, event, None, recovery=None)

    # Worker is marked FAILED
    assert new_state.worker_states["w1"].status == WorkerStatus.FAILED
    # Phase unchanged
    assert new_state.phase == "development"
    assert effects == []
