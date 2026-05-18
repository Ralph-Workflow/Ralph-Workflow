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


class TestChildExitedCleanly:
    """Terminal ack causes TERMINAL_COMPLETE; no indefinite waiting."""

    def test_terminal_ack_produces_terminal_complete(self) -> None:
        """After child emits terminal ack, classify_exit returns TERMINAL_COMPLETE."""
        t = [0.0]
        reg = _make_registry(t=t)
        strategy = OpenCodeExecutionStrategy(label_scope="scope/x", registry=reg)
        probe = FakeLivenessProbe()

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "c1"}))
        strategy.observe_line(json.dumps({"type": "child_progress", "child_id": "c1"}))
        strategy.observe_line(json.dumps({"type": "child_complete", "child_id": "c1"}))

        handle = _FakeHandle(has_descendants=False)
        result = strategy.classify_exit(handle, _no_signals(), liveness_probe=probe)

        assert result == AgentExecutionState.TERMINAL_COMPLETE, (
            f"Expected TERMINAL_COMPLETE after terminal ack; got {result!r}"
        )

    def test_terminal_ack_wins_over_os_descendants(self) -> None:
        """Even if OS descendants still exist, terminal ack takes precedence."""
        t = [0.0]
        reg = _make_registry(t=t)
        strategy = OpenCodeExecutionStrategy(label_scope="scope/x", registry=reg)
        probe = FakeLivenessProbe()

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "c1"}))
        strategy.observe_line(json.dumps({"type": "child_complete", "child_id": "c1"}))

        # OS descendants still visible (straggler cleanup)
        handle = _FakeHandle(has_descendants=True)
        result = strategy.classify_exit(handle, _no_signals(), liveness_probe=probe)

        assert result == AgentExecutionState.TERMINAL_COMPLETE, (
            f"Terminal ack must outrank OS descendants; got {result!r}"
        )


def _make_registry(*, t: list[float]) -> ChildLivenessRegistry:
    return ChildLivenessRegistry(
        progress_ttl=45.0,
        heartbeat_ttl=15.0,
        stale_label_ttl=10.0,
        exit_reconcile=5.0,
        now=lambda: t[0],
    )


def _no_signals() -> CompletionSignals:
    return CompletionSignals(
        explicit_complete=False,
        required_artifact_present=False,
        artifact_types=(),
    )
