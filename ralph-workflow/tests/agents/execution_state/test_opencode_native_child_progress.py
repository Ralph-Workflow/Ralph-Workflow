"""``OpenCodeExecutionStrategy.record_native_child_progress`` public seam."""

from __future__ import annotations

from ralph.agents.execution_state import AgentExecutionState, OpenCodeExecutionStrategy
from ralph.agents.timeout_clock import FakeClock
from ralph.process.child_liveness import ChildLivenessRegistry
from ralph.process.liveness import FakeLivenessProbe
from tests.fake_handle import _FakeHandle


def _registry(clock: FakeClock) -> ChildLivenessRegistry:
    return ChildLivenessRegistry(
        progress_ttl=45.0,
        heartbeat_ttl=15.0,
        stale_label_ttl=10.0,
        exit_reconcile=5.0,
        now=clock.monotonic,
    )


def test_out_of_band_child_progress_defers_the_quiet_parent() -> None:
    clock = FakeClock(start=10.0)
    registry = _registry(clock)
    strategy = OpenCodeExecutionStrategy(label_scope="run-1", registry=registry)
    handle = _FakeHandle(has_descendants=False)
    probe = FakeLivenessProbe(active=False)

    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.ACTIVE

    strategy.record_native_child_progress("ses_child")
    assert registry.has_child("ses_child")
    assert registry.has_records("agent:run-1:")
    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.WAITING_ON_CHILD

    clock.advance(12.0)
    strategy.record_native_child_progress("ses_child")
    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.WAITING_ON_CHILD

    clock.advance(60.0)
    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.ACTIVE, (
        "a child that stopped writing is stale evidence, not a reason to keep waiting"
    )


def test_repeated_progress_does_not_reset_the_child_start_time() -> None:
    clock = FakeClock(start=10.0)
    registry = _registry(clock)
    strategy = OpenCodeExecutionStrategy(label_scope=None, registry=registry)

    strategy.record_native_child_progress("ses_child")
    clock.advance(30.0)
    strategy.record_native_child_progress("ses_child")
    snapshot = registry.snapshot("")
    assert snapshot.active_count == 1
    assert snapshot.oldest_live_child_seconds == 30.0


def test_without_a_registry_the_seam_is_a_no_op() -> None:
    strategy = OpenCodeExecutionStrategy()
    strategy.record_native_child_progress("ses_child")
    assert (
        strategy.classify_quiet(_FakeHandle(), FakeLivenessProbe(active=False))
        == AgentExecutionState.ACTIVE
    )
