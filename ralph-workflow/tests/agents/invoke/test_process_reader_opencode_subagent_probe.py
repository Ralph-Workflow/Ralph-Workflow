"""The subprocess reader watches OpenCode's session store for native subagents.

Drives ``read_lines_from_process`` with a parent that prints its dispatch
frames and then blocks, while an injected child-part source reports fresh
store updates for 900 s of fake time. The idle watchdog (300 s idle timeout)
must not fire while the child is working and must fire once it stops.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from ralph.agents.execution_state import OpenCodeExecutionStrategy
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import IdleStreamTimeoutError, ProcessReaderCtx, read_lines_from_process
from ralph.agents.invoke.opencode_subagent_sessions import OpenCodeChildPart
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.process.child_liveness import ChildLivenessRegistry
from ralph.process.liveness import FakeLivenessProbe

_PARENT_LINES = (
    '{"type":"step_start","sessionID":"ses_parent","part":{"type":"step-start"}}\n',
    '{"type":"text","sessionID":"ses_parent","part":{"type":"text","text":"Delegating"}}\n',
    '{"type":"step_start","sessionID":"ses_parent","part":{"type":"step-start"}}\n',
)


class _ChildSource:
    def __init__(self, clock: FakeClock, active_until: float) -> None:
        self._clock = clock
        self._active_until = active_until
        self.parents: list[str] = []
        self.closed = False
        self._n = 0

    def fetch(self, parent_session_id: str, since_ms: int) -> list[OpenCodeChildPart]:
        del since_ms
        self.parents.append(parent_session_id)
        now = self._clock.monotonic()
        if now > self._active_until:
            return []
        self._n += 1
        return [
            OpenCodeChildPart("ses_child", "Sisyphus-Junior", "Fix gate", f"prt_{self._n}", "tool:bash", int(now * 1000))
        ]

    def close(self) -> None:
        self.closed = True


def _read_nothing(_size: int) -> str:
    return ""


@pytest.mark.timeout_seconds(5)
def test_store_updates_keep_a_silent_opencode_parent_alive() -> None:
    stop_event = threading.Event()

    class _Stdout:
        def __init__(self) -> None:
            self._pending = list(_PARENT_LINES)

        def __iter__(self) -> _Stdout:
            return self

        def __next__(self) -> str:
            if self._pending:
                return self._pending.pop(0)
            stop_event.wait(10)
            raise StopIteration

    class _Handle:
        returncode: int | None = None
        stdout = _Stdout()
        stderr = SimpleNamespace(read=_read_nothing)
        pid: int = 999_998
        terminate_count = 0

        def terminate(self, grace_period_s: float | None = None) -> None:
            del grace_period_s
            self.terminate_count += 1
            stop_event.set()
            self.returncode = -15

        def wait(self, timeout: float | None = None) -> int | None:
            del timeout
            return self.returncode

        def poll(self) -> int | None:
            return self.returncode

    clock = FakeClock(start=0.0)
    registry = ChildLivenessRegistry(
        progress_ttl=45.0, heartbeat_ttl=15.0, stale_label_ttl=10.0, exit_reconcile=5.0, now=clock.monotonic
    )
    strategy = OpenCodeExecutionStrategy(label_scope=None, registry=registry)
    source = _ChildSource(clock, active_until=900.0)
    handle = _Handle()

    with pytest.raises(IdleStreamTimeoutError):
        list(
            read_lines_from_process(
                handle,
                ctx=ProcessReaderCtx(
                    config=AgentConfig(cmd="opencode", transport=AgentTransport.OPENCODE),
                    policy=TimeoutPolicy(
                        idle_timeout_seconds=300.0,
                        drain_window_seconds=0.0,
                        idle_poll_interval_seconds=5.0,
                        max_session_seconds=None,
                    ),
                    execution_strategy=strategy,
                    liveness_probe=FakeLivenessProbe(active=False),
                    opencode_child_part_source=source,
                ),
                _clock=clock,
            )
        )

    assert clock.monotonic() >= 900.0, "the watchdog must not fire while the child keeps writing"
    assert clock.monotonic() <= 900.0 + 300.0 + 600.0, (
        "once the child goes quiet the idle timeout (300 s) and the no-progress ceiling (600 s) apply"
    )
    assert set(source.parents) == {"ses_parent"}, "the captured parent sessionID scopes the store query"
    assert len(source.parents) > 10, "the store is polled throughout the silent window, not once"
    assert handle.terminate_count == 1
    assert source.closed is True
