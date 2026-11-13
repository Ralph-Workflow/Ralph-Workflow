from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from ralph.agents.execution_state import (
    AgentExecutionState,
    OpenCodeExecutionStrategy,
)
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    IdleStreamTimeoutError,
    ProcessReaderCtx,
    read_lines_from_process,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.process.liveness import FakeLivenessProbe

_DESCENDANT_WAIT_POLL_SECONDS = 0.5


class TestReadLinesFromProcessWaitingOnChildDeferred:
    stdin = None

    def test_waiting_on_child_defers_then_active_fires(self) -> None:

        stop_event = threading.Event()

        class _BlockingStdout:
            def __iter__(self) -> _BlockingStdout:
                return self

            def __next__(self) -> str:
                stop_event.wait(10)
                raise StopIteration

        class _TestHandle:
            returncode: int | None = None
            stdout = _BlockingStdout()
            stderr = SimpleNamespace(read=lambda _size: "")
            terminate_count: int = 0
            pid: int = 999_999

            def terminate(self, grace_period_s: float | None = None, **_kwargs: object) -> None:
                del grace_period_s
                self.terminate_count += 1
                stop_event.set()
                self.returncode = -15

            def __enter__(self) -> _TestHandle:
                return self

            def __exit__(self, *_: object) -> bool:
                return False

            def wait(self, timeout: float | None = None) -> int | None:
                del timeout
                return self.returncode

            def poll(self) -> int | None:
                return self.returncode

        handle = _TestHandle()

        class _OnceThenActive(OpenCodeExecutionStrategy):
            def __init__(self) -> None:
                self.call_count = 0

            def classify_quiet(self, handle: object, liveness_probe: object) -> AgentExecutionState:
                self.call_count += 1
                if self.call_count == 1:
                    return AgentExecutionState.WAITING_ON_CHILD
                return AgentExecutionState.ACTIVE

        strategy = _OnceThenActive()
        probe = FakeLivenessProbe(active=False)
        fake_clock = FakeClock(start=0.0)

        with pytest.raises(IdleStreamTimeoutError):
            list(
                read_lines_from_process(
                    handle,
                    ctx=ProcessReaderCtx(
                        config=AgentConfig(
                            cmd="opencode",
                            transport=AgentTransport.OPENCODE,
                        ),
                        policy=TimeoutPolicy(
                            idle_timeout_seconds=1.0,
                            drain_window_seconds=0.0,
                            max_waiting_on_child_seconds=1800.0,
                        ),
                        execution_strategy=strategy,
                        liveness_probe=probe,
                    ),
                    _clock=fake_clock,
                )
            )

        assert handle.terminate_count == 1, f"Expected 1 termination; got {handle.terminate_count}"
