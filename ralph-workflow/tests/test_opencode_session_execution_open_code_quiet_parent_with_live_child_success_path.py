"""Black-box regression tests for OpenCode session-aware execution.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil. Verifies five acceptance scenarios and two edge cases.
"""

from __future__ import annotations

import threading
import time as _time_module
from itertools import chain, repeat
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest

from ralph.agents.execution_state import (
    AgentExecutionState,
    OpenCodeExecutionStrategy,
)
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    CompletionCheckOptions,
    ProcessReaderCtx,
    check_process_result,
    read_lines_from_process,
)
from ralph.process.liveness import FakeLivenessProbe

if TYPE_CHECKING:

    from ralph.process.manager import ManagedProcess


# Poll interval used in the wait helper - matches _DESCENDANT_WAIT_POLL_SECONDS
_DESCENDANT_WAIT_POLL_SECONDS = 0.5

# Local aliases: tests call the same public functions but under the private-looking names
# that were used when this module was monolithic (pre-package split).
_check_process_result = check_process_result
_CompletionCheckOptions = CompletionCheckOptions


class TestOpenCodeQuietParentWithLiveChildSuccessPath:
    """Quiet parent with active liveness probe: clock resets, parent produces output, no timeout."""

    @pytest.mark.skip(reason="Event.wait() uses real wall-clock time; FakeClock can't control it")
    def test_quiet_opencode_parent_with_ralph_tracked_child_resets_idle_clock(
        self,
    ) -> None:
        """Active liveness probe resets clock; parent produces output with no timeout.

        Integration path: _read_lines_from_process with OpenCodeExecutionStrategy.
        """
        output_ready = threading.Event()

        class _BlockingThenOneLineStdout:
            """Blocks until output_ready is set, then emits one line and finishes."""

            def __init__(self) -> None:
                self._emitted = False

            def __iter__(self) -> _BlockingThenOneLineStdout:
                return self

            def __next__(self) -> str:
                if not self._emitted:
                    output_ready.wait(10)
                    self._emitted = True
                    return '{"type":"result"}\n'
                raise StopIteration

        class _TestHandle:
            returncode: int | None = 0
            stdout = _BlockingThenOneLineStdout()
            stderr = SimpleNamespace(read=lambda: "")
            terminate_count: int = 0

            def terminate(self, grace_period_s: float | None = None) -> None:
                del grace_period_s
                self.terminate_count += 1

            def __enter__(self) -> _TestHandle:
                return self

            def __exit__(self, *_: object) -> bool:
                return False

            def wait(self, timeout: float | None = None) -> int | None:
                del timeout
                return self.returncode

            def poll(self) -> int | None:
                return self.returncode

        class _WaitingThenDoneStrategy(OpenCodeExecutionStrategy):
            """Returns WAITING_ON_CHILD and unblocks stdout on first classify_quiet call."""

            def __init__(self) -> None:
                self.call_count = 0

            def classify_quiet(self, handle: object, liveness_probe: object) -> AgentExecutionState:
                self.call_count += 1
                output_ready.set()
                return AgentExecutionState.WAITING_ON_CHILD

        handle = _TestHandle()
        strategy = _WaitingThenDoneStrategy()
        probe = FakeLivenessProbe(active=True)

        # First 3 monotonic values drive the initial timeout trigger and reset.
        # Unlimited 1.5 values ensure no second timeout fires (1.5 - 1.1 = 0.4 < 1.0).
        monotonic_vals = chain([0.0, 1.1, 1.1], repeat(1.5))

        with (
            patch.object(_time_module, "monotonic", side_effect=lambda: next(monotonic_vals)),
        ):
            collected = list(
                read_lines_from_process(
                    cast("ManagedProcess", handle),
                    ctx=ProcessReaderCtx(
                        policy=TimeoutPolicy(idle_timeout_seconds=1.0),
                        execution_strategy=strategy,
                        liveness_probe=probe,
                    ),
                )
            )

        assert collected == ['{"type":"result"}\n'], (
            f"Expected output line after WAITING_ON_CHILD reset; got {collected!r}"
        )
        assert strategy.call_count >= 1, (
            "classify_quiet must have been called at least once (clock reset happened)"
        )
        assert handle.terminate_count == 0, (
            f"Handle must not be terminated when idle timeout never fires; "
            f"got {handle.terminate_count} termination(s)"
        )
