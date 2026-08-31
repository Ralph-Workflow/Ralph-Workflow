"""A conflict-resolution reader returns once its OpenCode parent has exited.

Replays the frame shape OpenCode 1.18 really emits: a retried message with
two ``step_start`` frames and one ``step_finish``, a final turn that ends
with text and NO trailing ``step_finish`` on stdout, then EOF with exit 0.
Under the ``activity_only`` profile the drain has no deadline and keeps
consulting ``classify_quiet``; before the fix the unbalanced step frames
pinned it to ``WAITING_ON_CHILD`` and the reader spun until the 900 s
inactivity ceiling fired on a parent that had finished its work.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ralph.agents.execution_state import OpenCodeExecutionStrategy
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.idle_watchdog.timeout_policy import TimeoutProfile
from ralph.agents.invoke import ProcessReaderCtx, read_lines_from_process
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.process.liveness import FakeLivenessProbe

if TYPE_CHECKING:
    from ralph.agents.invoke.opencode_subagent_sessions import OpenCodeChildPart

_PARENT_LINES = (
    '{"type":"step_start","sessionID":"ses_parent","part":{"messageID":"msg_a"}}\n',
    '{"type":"step_start","sessionID":"ses_parent","part":{"messageID":"msg_a"}}\n',
    '{"type":"tool_use","sessionID":"ses_parent","part":{"type":"tool","tool":"ralph_mcp__ralph__declare_complete","state":{"status":"completed"}}}\n',
    '{"type":"step_finish","sessionID":"ses_parent","part":{"messageID":"msg_a","reason":"tool-calls"}}\n',
    '{"type":"step_start","sessionID":"ses_parent","part":{"messageID":"msg_b"}}\n',
    '{"type":"text","sessionID":"ses_parent","part":{"type":"text","text":"Resolved the conflict."}}\n',
)

_INACTIVITY_TIMEOUT_SECONDS = 900.0


class _NoChildren:
    def fetch(self, parent_session_id: str, since_ms: int) -> list[OpenCodeChildPart]:
        del parent_session_id, since_ms
        return []

    def close(self) -> None:
        return


def _read_nothing(_size: int) -> str:
    return ""


class _Handle:
    """A parent that prints its frames, closes stdout and exits 0."""

    def __init__(self) -> None:
        self.stdout = iter(_PARENT_LINES)
        self.stderr = SimpleNamespace(read=_read_nothing)
        self.pid = 999_997
        self.returncode: int | None = 0
        self.terminate_count = 0

    def terminate(self, grace_period_s: float | None = None) -> None:
        del grace_period_s
        self.terminate_count += 1

    def wait(self, timeout: float | None = None) -> int | None:
        del timeout
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode


@pytest.mark.timeout_seconds(5)
def test_activity_only_reader_returns_when_the_opencode_parent_exits_mid_step() -> None:
    clock = FakeClock(start=0.0)
    handle = _Handle()

    lines = list(
        read_lines_from_process(
            handle,
            ctx=ProcessReaderCtx(
                config=AgentConfig(cmd="opencode", transport=AgentTransport.OPENCODE),
                policy=TimeoutPolicy(
                    profile=TimeoutProfile.ACTIVITY_ONLY,
                    idle_timeout_seconds=_INACTIVITY_TIMEOUT_SECONDS,
                    drain_window_seconds=0.0,
                    idle_poll_interval_seconds=5.0,
                    max_session_seconds=None,
                ),
                execution_strategy=OpenCodeExecutionStrategy(label_scope=None, registry=None),
                liveness_probe=FakeLivenessProbe(active=False),
                opencode_child_part_source=_NoChildren(),
            ),
            _clock=clock,
        )
    )

    assert [line.rstrip("\n") for line in lines] == [line.rstrip("\n") for line in _PARENT_LINES]
    assert clock.monotonic() < _INACTIVITY_TIMEOUT_SECONDS, (
        "the reader must return as soon as the exited parent's stream is drained, "
        "not spin until the inactivity ceiling"
    )
    assert handle.terminate_count == 0
