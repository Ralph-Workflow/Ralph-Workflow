from __future__ import annotations

import json

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import AgentExecutionState, OpenCodeExecutionStrategy
from ralph.process.child_liveness import ChildLivenessRegistry
from ralph.process.liveness import FakeLivenessProbe
from tests.integration.fake_handle import _FakeHandle


class TestChildExitedCleanly:
    def test_terminal_ack_without_parent_evidence_is_resumable(self) -> None:
        t = [0.0]
        reg = _make_registry(t=t)
        strategy = OpenCodeExecutionStrategy(label_scope="scope/x", registry=reg)
        probe = FakeLivenessProbe()

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "c1"}))
        strategy.observe_line(json.dumps({"type": "child_progress", "child_id": "c1"}))
        strategy.observe_line(json.dumps({"type": "child_complete", "child_id": "c1"}))

        handle = _FakeHandle(has_descendants=False)
        result = strategy.classify_exit(handle, _no_signals(), liveness_probe=probe)

        assert result == AgentExecutionState.RESUMABLE_CONTINUE, (
            f"Terminal child ack without parent evidence must resume; got {result!r}"
        )

    def test_terminal_ack_prevents_os_descendants_from_deferring_parent(self) -> None:
        t = [0.0]
        reg = _make_registry(t=t)
        strategy = OpenCodeExecutionStrategy(label_scope="scope/x", registry=reg)
        probe = FakeLivenessProbe()

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "c1"}))
        strategy.observe_line(json.dumps({"type": "child_complete", "child_id": "c1"}))

        handle = _FakeHandle(has_descendants=True)
        result = strategy.classify_exit(handle, _no_signals(), liveness_probe=probe)

        assert result == AgentExecutionState.RESUMABLE_CONTINUE, (
            f"Terminal child ack must not defer or complete the parent; got {result!r}"
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
