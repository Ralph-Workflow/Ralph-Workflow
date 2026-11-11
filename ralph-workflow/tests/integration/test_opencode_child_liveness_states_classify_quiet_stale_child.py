from __future__ import annotations

import json

from ralph.agents.execution_state import AgentExecutionState, OpenCodeExecutionStrategy
from ralph.process.child_liveness import ChildActivitySnapshot, ChildLivenessRegistry
from ralph.process.liveness import FakeLivenessProbe
from tests.integration.fake_handle import _FakeHandle


class TestClassifyQuietStaleChild:
    def test_stale_probe_snapshot_without_fresh_evidence_is_not_waiting(self) -> None:
        t = [0.0]
        reg = _make_registry(t=t)
        strategy = OpenCodeExecutionStrategy(label_scope="scope/stale", registry=reg)

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "stale1"}))
        t[0] = 60.0

        stale_snapshot = ChildActivitySnapshot(
            scope_prefix="scope/stale",
            has_process=True,
            has_fresh_label=False,
            has_fresh_progress=False,
            oldest_live_child_seconds=None,
            active_count=0,
            terminal_count=0,
        )
        probe = FakeLivenessProbe(snapshot=stale_snapshot)

        handle = _FakeHandle(has_descendants=False)
        result = strategy.classify_quiet(handle, probe)

        assert result != AgentExecutionState.WAITING_ON_CHILD, (
            f"Stale child without fresh evidence must NOT yield WAITING_ON_CHILD; got {result!r}. "
            "This was the root cause of wt-97: stale has_process kept watchdog in WAITING_ON_CHILD."
        )

    def test_fresh_progress_via_probe_still_holds_waiting(self) -> None:
        t = [0.0]
        reg = _make_registry(t=t)
        strategy = OpenCodeExecutionStrategy(label_scope="scope/fresh", registry=reg)

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "fresh1"}))
        t[0] = 10.0

        fresh_snapshot = ChildActivitySnapshot(
            scope_prefix="scope/fresh",
            has_process=True,
            has_fresh_label=True,
            has_fresh_progress=True,
            oldest_live_child_seconds=10.0,
            active_count=1,
            terminal_count=0,
        )
        probe = FakeLivenessProbe(snapshot=fresh_snapshot)

        handle = _FakeHandle(has_descendants=False)
        result = strategy.classify_quiet(handle, probe)

        assert result == AgentExecutionState.WAITING_ON_CHILD, (
            f"Fresh progress must still hold WAITING_ON_CHILD; got {result!r}"
        )

    def test_fresh_heartbeat_only_via_probe_still_holds_waiting(self) -> None:
        t = [0.0]
        reg = _make_registry(t=t)
        strategy = OpenCodeExecutionStrategy(label_scope="scope/hb", registry=reg)

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "hb1"}))
        t[0] = 10.0

        heartbeat_only_snapshot = ChildActivitySnapshot(
            scope_prefix="scope/hb",
            has_process=True,
            has_fresh_label=True,
            has_fresh_progress=False,
            oldest_live_child_seconds=10.0,
            active_count=1,
            terminal_count=0,
        )
        probe = FakeLivenessProbe(snapshot=heartbeat_only_snapshot)

        handle = _FakeHandle(has_descendants=False)
        result = strategy.classify_quiet(handle, probe)

        assert result == AgentExecutionState.WAITING_ON_CHILD, (
            f"Fresh heartbeat-only must still hold WAITING_ON_CHILD; got {result!r}"
        )

    def test_stale_child_with_os_descendants_returns_active(self) -> None:
        t = [0.0]
        reg = _make_registry(t=t)
        strategy = OpenCodeExecutionStrategy(label_scope="scope/os", registry=reg)

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "os1"}))
        t[0] = 60.0

        stale_snapshot = ChildActivitySnapshot(
            scope_prefix="scope/os",
            has_process=True,
            has_fresh_label=False,
            has_fresh_progress=False,
            oldest_live_child_seconds=None,
            active_count=0,
            terminal_count=0,
        )
        probe = FakeLivenessProbe(snapshot=stale_snapshot)

        handle = _FakeHandle(has_descendants=True)
        result = strategy.classify_quiet(handle, probe)

        assert result == AgentExecutionState.ACTIVE, (
            f"Stale scoped evidence + OS descendants must yield ACTIVE; got {result!r}"
        )


def _make_registry(*, t: list[float]) -> ChildLivenessRegistry:
    return ChildLivenessRegistry(
        progress_ttl=45.0,
        heartbeat_ttl=15.0,
        stale_label_ttl=10.0,
        exit_reconcile=5.0,
        now=lambda: t[0],
    )
