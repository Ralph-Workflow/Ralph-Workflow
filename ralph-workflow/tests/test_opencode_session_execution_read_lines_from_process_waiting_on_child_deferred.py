"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from ralph.agents.execution_state import (
    AgentExecutionState,
    OpenCodeExecutionStrategy,
)
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    CompletionCheckOptions,
    IdleStreamTimeoutError,
    ProcessReaderCtx,
    check_process_result,
    read_lines_from_process,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.process.liveness import FakeLivenessProbe

if TYPE_CHECKING:

    from ralph.process.manager import ManagedProcess


# Poll interval used in the wait helper - matches _DESCENDANT_WAIT_POLL_SECONDS
_DESCENDANT_WAIT_POLL_SECONDS = 0.5

# Local aliases: tests call the same public functions but under the private-looking names
# that were used when this module was monolithic (pre-package split).
_check_process_result = check_process_result
_CompletionCheckOptions = CompletionCheckOptions


class TestReadLinesFromProcessWaitingOnChildDeferred:
    """_read_lines_from_process defers termination when classify_quiet returns WAITING_ON_CHILD."""

    def test_waiting_on_child_defers_then_active_fires(self) -> None:
        """WAITING_ON_CHILD defers; handle is only terminated when ACTIVE fires afterward."""

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
            stderr = SimpleNamespace(read=lambda: "")
            terminate_count: int = 0

            def terminate(self, grace_period_s: float | None = None) -> None:
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
            """Returns WAITING_ON_CHILD on first classify_quiet call, ACTIVE on subsequent."""

            def __init__(self) -> None:
                self.call_count = 0

            def classify_quiet(self, handle: object, liveness_probe: object) -> AgentExecutionState:
                self.call_count += 1
                if self.call_count == 1:
                    return AgentExecutionState.WAITING_ON_CHILD
                return AgentExecutionState.ACTIVE

        strategy = _OnceThenActive()
        probe = FakeLivenessProbe(active=False)
        # FakeClock advances time on every wait_for_event call, no real wall time.
        # With idle_timeout=1.0 and poll interval 0.05s, the watchdog fires after ~20 ticks.
        # drain_window=0.0 fires immediately on ACTIVE (no re-consultation loop).
        fake_clock = FakeClock(start=0.0)

        with pytest.raises(IdleStreamTimeoutError):
            list(
                read_lines_from_process(
                    cast("ManagedProcess", handle),
                    ctx=ProcessReaderCtx(
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

        # Handle was terminated exactly once (on ACTIVE fire, not on WAITING_ON_CHILD deferral).
        assert handle.terminate_count == 1, f"Expected 1 termination; got {handle.terminate_count}"
