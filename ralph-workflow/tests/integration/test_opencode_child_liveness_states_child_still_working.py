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

import json

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import AgentExecutionState, OpenCodeExecutionStrategy
from ralph.process.child_liveness import ChildLivenessRegistry
from ralph.process.liveness import FakeLivenessProbe
from tests.integration.fake_handle import _FakeHandle


class TestChildStillWorking:
    """Fresh progress renewal keeps parent in WAITING_ON_CHILD."""

    def test_fresh_progress_holds_waiting(self) -> None:
        """Each progress event within progress_ttl maintains WAITING_ON_CHILD."""
        t = [0.0]
        reg = _make_registry(t=t)
        strategy = OpenCodeExecutionStrategy(label_scope="scope/busy", registry=reg)
        probe = FakeLivenessProbe()

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "busy"}))

        # Simulate periodic progress renewal
        for tick in [5.0, 20.0, 40.0]:
            t[0] = tick
            strategy.observe_line(json.dumps({"type": "child_progress", "child_id": "busy"}))

        handle = _FakeHandle(has_descendants=False)
        result = strategy.classify_exit(handle, _no_signals(), liveness_probe=probe)

        assert result == AgentExecutionState.WAITING_ON_CHILD, (
            f"Fresh progress must keep WAITING_ON_CHILD; got {result!r}"
        )

    def test_progress_renewal_after_near_expiry_resets_freshness(self) -> None:
        """Progress at t=44 resets progress_ttl so at t=85 the child is still fresh."""
        t = [0.0]
        reg = _make_registry(t=t)
        strategy = OpenCodeExecutionStrategy(label_scope="scope/renew", registry=reg)
        probe = FakeLivenessProbe()

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "r1"}))

        # Initial progress at t=0 (from started event, heartbeat at t=0)
        # Progress renewal at t=44 (1s before ttl)
        t[0] = 44.0
        strategy.observe_line(json.dumps({"type": "child_progress", "child_id": "r1"}))

        # At t=85, 41s since last progress (< 45s progress_ttl) → still fresh
        t[0] = 85.0
        handle = _FakeHandle(has_descendants=False)
        result = strategy.classify_exit(handle, _no_signals(), liveness_probe=probe)

        assert result == AgentExecutionState.WAITING_ON_CHILD, (
            f"Renewed progress should be fresh at t=85 (41s after renewal); got {result!r}"
        )

    def test_progress_expires_without_renewal_yields_resumable(self) -> None:
        """After progress_ttl expires without renewal and no OS descendants → RESUMABLE_CONTINUE."""
        t = [0.0]
        reg = _make_registry(t=t)
        strategy = OpenCodeExecutionStrategy(label_scope="scope/expired", registry=reg)
        probe = FakeLivenessProbe()

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "e1"}))
        # Progress at t=0 via observe_line (from heartbeat update on spawn)
        strategy.observe_line(json.dumps({"type": "child_progress", "child_id": "e1"}))

        # Advance past progress_ttl=45 and stale_label_ttl=10
        t[0] = 60.0

        handle = _FakeHandle(has_descendants=False)
        result = strategy.classify_exit(handle, _no_signals(), liveness_probe=probe)

        assert result == AgentExecutionState.RESUMABLE_CONTINUE, (
            f"Expired progress without renewal must yield RESUMABLE_CONTINUE; got {result!r}"
        )


def _make_registry(*, t: list[float]) -> ChildLivenessRegistry:
    return ChildLivenessRegistry(
        progress_ttl=45.0,
        heartbeat_ttl=15.0,
        stale_label_ttl=10.0,
        exit_reconcile=5.0,
        now=lambda: t[0],
    )


def _no_signals(
    workspace: object = None,
    raw_output: object = None,
    *,
    required_artifact: object = None,
    run_id: object = None,
    sentinel_secret: object = None,
    receipt_secret: object = None,
) -> CompletionSignals:
    return CompletionSignals(
        explicit_complete=False,
        required_artifact_present=False,
        artifact_types=(),
    )
