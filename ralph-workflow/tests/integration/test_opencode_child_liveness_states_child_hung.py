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


class TestChildHung:
    """Stale process existence alone must NOT hold WAITING_ON_CHILD open.

    When a child was spawned but has not renewed its progress lease within
    progress_ttl, and the stale_label_ttl has also expired, the registry
    provides no fresh evidence.  The parent must NOT keep WAITING_ON_CHILD
    based solely on the stale registry record.  Without OS descendants,
    the result must be RESUMABLE_CONTINUE.
    """

    def test_stale_process_without_fresh_evidence_is_not_waiting(self) -> None:
        """After progress_ttl + stale_label_ttl expire, stale child → RESUMABLE_CONTINUE."""
        t = [0.0]
        reg = _make_registry(t=t)
        strategy = OpenCodeExecutionStrategy(label_scope="scope/hung", registry=reg)
        probe = FakeLivenessProbe()  # no active labels

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "hung"}))

        # Advance past both stale_label_ttl (10s) and progress_ttl (45s)
        t[0] = 60.0

        handle = _FakeHandle(has_descendants=False)
        result = strategy.classify_exit(handle, _no_signals(), liveness_probe=probe)

        assert result == AgentExecutionState.RESUMABLE_CONTINUE, (
            f"Stale child without fresh evidence must yield RESUMABLE_CONTINUE; got {result!r}"
        )

    def test_stale_process_with_os_descendants_resumable(self) -> None:
        """Stale registry + live OS descendants → RESUMABLE_CONTINUE (stale scoped wins).

        This is the wt-97 fix: when scoped Ralph child evidence is stale (child
        registered but no fresh progress/label), raw OS descendant presence alone
        must NOT override staleness. The timeout must fire via RESUMABLE_CONTINUE.
        """
        t = [0.0]
        reg = _make_registry(t=t)
        strategy = OpenCodeExecutionStrategy(label_scope="scope/hung", registry=reg)
        probe = FakeLivenessProbe()

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "hung"}))
        t[0] = 60.0

        handle = _FakeHandle(has_descendants=True)
        result = strategy.classify_exit(handle, _no_signals(), liveness_probe=probe)

        assert result == AgentExecutionState.RESUMABLE_CONTINUE, (
            f"Stale scoped evidence + OS descendants must yield RESUMABLE_CONTINUE; got {result!r}"
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
