from __future__ import annotations

from types import SimpleNamespace

from ralph.agents.execution_state import (
    AgentExecutionState,
    OpenCodeExecutionStrategy,
)
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import ProcessReaderCtx, read_lines_from_process
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.process.liveness import FakeLivenessProbe


class TestOpenCodeQuietParentWithLiveChildSuccessPath:
    def test_quiet_opencode_parent_with_ralph_tracked_child_resets_idle_clock(
        self,
    ) -> None:

        class _OneLineThenDoneStdout:
            def __init__(self) -> None:
                self._emitted = False

            def __iter__(self) -> _OneLineThenDoneStdout:
                return self

            def __next__(self) -> str:
                if not self._emitted:
                    self._emitted = True
                    return '{"type":"result"}\n'
                raise StopIteration

        class _TestHandle:
            returncode: int | None = 0
            stdout = _OneLineThenDoneStdout()
            stderr = SimpleNamespace(read=lambda _size: "")
            terminate_count: int = 0
            pid: int | None = None
            _poll_count: int = 0

            def terminate(self, grace_period_s: float | None = None, **_kwargs: object) -> None:
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
                self._poll_count += 1
                if self._poll_count < 3:
                    return None
                return self.returncode

        class _WaitingThenDoneStrategy(OpenCodeExecutionStrategy):
            def __init__(self) -> None:
                super().__init__()
                self.call_count = 0

            def classify_quiet(self, handle: object, liveness_probe: object) -> AgentExecutionState:
                del handle, liveness_probe
                self.call_count += 1
                return AgentExecutionState.WAITING_ON_CHILD

        handle = _TestHandle()
        strategy = _WaitingThenDoneStrategy()
        probe = FakeLivenessProbe(active=True)
        clock = FakeClock(start=0.0)

        collected = list(
            read_lines_from_process(
                handle,
                ctx=ProcessReaderCtx(
                    config=AgentConfig(
                        cmd="opencode",
                        transport=AgentTransport.OPENCODE,
                    ),
                    policy=TimeoutPolicy(idle_timeout_seconds=0.01),
                    execution_strategy=strategy,
                    liveness_probe=probe,
                ),
                _clock=clock,
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
