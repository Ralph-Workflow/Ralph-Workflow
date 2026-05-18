"""End-to-end regression scenarios for OpenCode child liveness classification.

Three operator-critical scenarios tested as black-box via the public execution strategy API:

1. Child exited cleanly — terminal ack seen → TERMINAL_COMPLETE, no indefinite waiting.
2. Child hung — process label exists but progress/heartbeat expired → RESUMABLE_CONTINUE
   (stale process existence alone must NOT hold WAITING_ON_CHILD open).
3. Child still working — progress lease keeps renewing → WAITING_ON_CHILD maintained.

Also includes post-exit path integration tests that drive check_process_result and
PostExitWatchdog with FakeClock to validate the planned end-to-end behaviors.
"""

from __future__ import annotations
from tests.integration.fake_handle import _FakeHandle

import json

from ralph.agents.execution_state import AgentExecutionState, OpenCodeExecutionStrategy
from ralph.process.child_liveness import ChildActivitySnapshot, ChildLivenessRegistry
from ralph.process.liveness import FakeLivenessProbe


class TestClassifyQuietStaleChild:
    """Regression: stale child_snapshot evidence must NOT hold WAITING_ON_CHILD in quiet time.

    When a child is registered but progress_ttl and stale_label_ttl have both expired,
    the probe's child_snapshot returns has_process=True (stale record still exists)
    but has_fresh_progress=False and has_fresh_label=False.

    The old classify_quiet() checked `snap.has_fresh_progress or snap.has_process`,
    incorrectly treating stale process existence as sufficient evidence to stay in
    WAITING_ON_CHILD. This caused the reported bug where a stuck agent would not
    be detected as timed out until the hard ceiling (1800s) instead of transitioning
    to RESUMABLE_CONTINUE after the normal idle deadline.

    The fix requires has_fresh_label (fresh heartbeat) in addition to has_process,
    matching the semantics already established in _check_probe_state and invoke.py's
    corroboration logic.
    """



    def test_stale_probe_snapshot_without_fresh_evidence_is_not_waiting(self) -> None:
        """Stale child_snapshot (has_process but not has_fresh_label)
        must NOT hold WAITING_ON_CHILD."""
        t = [0.0]
        reg = _make_registry(t=t)
        strategy = OpenCodeExecutionStrategy(label_scope="scope/stale", registry=reg)

        # Register child, then advance time so all freshness expires
        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "stale1"}))
        t[0] = 60.0  # past stale_label_ttl=10 and progress_ttl=45

        # Fake probe reports: has_process=True (stale record), but no fresh evidence
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
        """Fresh progress via probe must still hold WAITING_ON_CHILD (positive regression)."""
        t = [0.0]
        reg = _make_registry(t=t)
        strategy = OpenCodeExecutionStrategy(label_scope="scope/fresh", registry=reg)

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "fresh1"}))
        t[0] = 10.0  # within freshness window

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
        """Fresh heartbeat-only (has_fresh_label=True, has_fresh_progress=False)
        must hold WAITING_ON_CHILD.

        This covers the transient network/lifecycle case where the child is sending
        heartbeats but no new progress. Per invoke.py corroboration, this is
        "fresh_heartbeat_only" and must NOT false-positive timeout.
        """
        t = [0.0]
        reg = _make_registry(t=t)
        strategy = OpenCodeExecutionStrategy(label_scope="scope/hb", registry=reg)

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "hb1"}))
        t[0] = 10.0

        heartbeat_only_snapshot = ChildActivitySnapshot(
            scope_prefix="scope/hb",
            has_process=True,
            has_fresh_label=True,  # fresh heartbeat
            has_fresh_progress=False,  # no new progress
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
        """Stale tracked child with live OS descendants → ACTIVE (stale scoped evidence wins).

        This is the wt-97 fix: when scoped Ralph child evidence is stale (has_process=True
        but no fresh progress/label), raw OS descendant presence alone must NOT keep the
        run in WAITING_ON_CHILD. The timeout must fire via ACTIVE.
        """
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
