"""Hypothesis stress tests for the parallel coordinator.

Generates random DAGs and asserts invariants after each run:
- All units end in SUCCEEDED, FAILED, or CANCELLED (never PENDING/RUNNING)
- SUCCEEDED units have all dependencies also SUCCEEDED
- Coordinator terminates without deadlock
"""

from __future__ import annotations

import asyncio
import random
from contextlib import suppress
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from hypothesis import given, settings  # pyright: ignore[reportMissingImports]
from hypothesis import strategies as st  # pyright: ignore[reportMissingImports]

from ralph.pipeline.effects import FanOutDevelopmentEffect
from ralph.pipeline.parallel import coordinator
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerStatus
from ralph.policy.models import PipelinePolicy
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun

WORKER_FAILURE_THRESHOLD = 0.2


def _build_acyclic_dag(n: int, edge_seed: int) -> list[WorkUnit]:
    """Build N work units with a random acyclic DAG.

    Lower-index units may depend only on higher-index units.
    """

    rng = random.Random(edge_seed)
    units = []
    for i in range(n):
        uid = f"unit-{i}"
        # Only depend on units with LOWER index to guarantee acyclicity
        possible_deps = [f"unit-{j}" for j in range(i)]
        # Pick 0-2 random deps from lower-index units
        k = min(len(possible_deps), rng.randint(0, 2))
        deps = rng.sample(possible_deps, k) if k > 0 else []
        units.append(WorkUnit(unit_id=uid, description=f"Work unit {uid}", dependencies=deps))
    return units


class _FakeDisplay:
    def emit(self, unit_id: str | None, line: str) -> None:
        pass

    def set_status(self, unit_id: str, status: object) -> None:
        pass


@given(
    n_units=st.integers(min_value=1, max_value=5),
    edge_seed=st.integers(min_value=0, max_value=99999),
    exit_seed=st.integers(min_value=0, max_value=99999),
)
@settings(max_examples=3, deadline=None)
def test_coordinator_all_units_reach_terminal_state(
    n_units: int,
    edge_seed: int,
    exit_seed: int,
) -> None:
    """Every unit must end in SUCCEEDED, FAILED, or CANCELLED — never PENDING or RUNNING."""

    rng = random.Random(exit_seed)

    units = _build_acyclic_dag(n_units, edge_seed)
    runs = {
        unit.unit_id: FakeRun(
            outputs=["ok"],
            exit_code=0 if rng.random() > WORKER_FAILURE_THRESHOLD else 1,
            duration_ms=1,
        )
        for unit in units
    }
    executor = FakeAgentExecutor(runs)
    effect = FanOutDevelopmentEffect(work_units=tuple(units), max_workers=n_units)
    state = PipelineState(work_units=tuple(units))
    display: Any = _FakeDisplay()
    checkpoint_path = Path("/tmp/test-stress-checkpoint.json")

    events = asyncio.run(
        asyncio.wait_for(
            coordinator.run_fan_out(effect, executor, display, checkpoint_path, state),
            timeout=30.0,
        )
    )

    dummy_policy = MagicMock(spec=PipelinePolicy)
    dummy_policy.phases = {}
    dummy_policy.max_iterations = 10
    dummy_policy.initial_drain = "dev"

    current = state
    for ev in events:
        with suppress(Exception):
            current, _ = reducer_reduce(current, ev, dummy_policy)

    terminal = {WorkerStatus.SUCCEEDED, WorkerStatus.FAILED, WorkerStatus.CANCELLED}
    for uid, ws in current.worker_states.items():
        assert ws.status in terminal, (
            f"Unit {uid!r} has non-terminal status {ws.status!r} after coordinator completed"
        )


@given(
    edge_seed=st.integers(min_value=0, max_value=99999),
)
@settings(max_examples=3, deadline=None)
def test_coordinator_succeeded_deps_also_succeeded(edge_seed: int) -> None:
    """Every SUCCEEDED unit must have all its dependencies also SUCCEEDED."""
    n = 5
    units = _build_acyclic_dag(n, edge_seed)
    runs = {unit.unit_id: FakeRun(outputs=["ok"], exit_code=0, duration_ms=1) for unit in units}
    executor = FakeAgentExecutor(runs)
    effect = FanOutDevelopmentEffect(work_units=tuple(units), max_workers=n)
    state = PipelineState(work_units=tuple(units))
    display: Any = _FakeDisplay()

    events = asyncio.run(
        asyncio.wait_for(
            coordinator.run_fan_out(effect, executor, display, Path("/tmp/c.json"), state),
            timeout=30.0,
        )
    )

    dummy_policy = MagicMock(spec=PipelinePolicy)
    dummy_policy.phases = {}
    dummy_policy.max_iterations = 10
    dummy_policy.initial_drain = "dev"

    current = state
    for ev in events:
        with suppress(Exception):
            current, _ = reducer_reduce(current, ev, dummy_policy)

    unit_map = {u.unit_id: u for u in units}
    for uid, ws in current.worker_states.items():
        if ws.status == WorkerStatus.SUCCEEDED:
            unit = unit_map.get(uid)
            if unit:
                for dep_id in unit.dependencies or []:
                    dep_ws = current.worker_states.get(dep_id)
                    if dep_ws:
                        assert dep_ws.status == WorkerStatus.SUCCEEDED, (
                            "Unit "
                            f"{uid!r} SUCCEEDED but dep {dep_id!r} "
                            f"has status {dep_ws.status!r}"
                        )
