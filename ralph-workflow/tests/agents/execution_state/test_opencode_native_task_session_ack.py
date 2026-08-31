"""A finished native ``task`` acks the store-keyed child record too.

OpenCode's completed ``task`` frame names the child by ``state.metadata.sessionId``;
the store probe registers that child under the same session id. Acking only
the ``callID`` record left the session-keyed one ``running`` until its
progress TTL pruned it, 45 s after the child had visibly finished.
"""

from __future__ import annotations

import json

from ralph.agents.execution_state import AgentExecutionState, OpenCodeExecutionStrategy
from ralph.agents.timeout_clock import FakeClock
from ralph.process.child_liveness import ChildLivenessRegistry
from ralph.process.liveness import FakeLivenessProbe
from tests.fake_handle import _FakeHandle


def _completed_task_frame(child_session_id: str) -> str:
    return json.dumps(
        {
            "type": "tool_use",
            "sessionID": "ses_parent",
            "part": {
                "type": "tool",
                "tool": "task",
                "callID": "call_1",
                "state": {
                    "status": "completed",
                    "metadata": {"parentSessionId": "ses_parent", "sessionId": child_session_id},
                },
            },
        }
    )


def test_completed_task_frame_acks_the_session_keyed_child() -> None:
    clock = FakeClock(start=10.0)
    registry = ChildLivenessRegistry(
        progress_ttl=45.0,
        heartbeat_ttl=15.0,
        stale_label_ttl=10.0,
        exit_reconcile=5.0,
        now=clock.monotonic,
    )
    strategy = OpenCodeExecutionStrategy(label_scope="run-1", registry=registry)
    handle = _FakeHandle(has_descendants=False)
    probe = FakeLivenessProbe(active=False)

    strategy.record_native_child_progress("ses_child")
    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.WAITING_ON_CHILD

    strategy.observe_line(_completed_task_frame("ses_child"))
    clock.advance(1.0)
    assert strategy.classify_quiet(handle, probe) == AgentExecutionState.ACTIVE, (
        "a child the stream reports finished must not be waited on until its TTL prunes it"
    )
