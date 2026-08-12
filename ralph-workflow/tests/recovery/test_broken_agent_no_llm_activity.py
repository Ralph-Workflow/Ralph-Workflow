"""Regression coverage for agents that emit activity without LLM output."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import AgentExecutionState, BaseExecutionStrategy
from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy
from ralph.agents.invoke import (
    BrokenAgentExitError,
    CompletionCheckOptions,
    check_broken_agent_timer,
    check_process_result,
    collect_r7_diagnostic_fields,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.process.manager import ManagedProcess
from ralph.timeout_defaults import BROKEN_AGENT_OUTPUT_GRACE_SECONDS


class _LiveHandle(ManagedProcess):
    @property
    def pid(self) -> int:
        return 0

    def __init__(self) -> None:
        self.terminated = False

    def terminate(self, grace_period_s: float | None = None) -> None:
        del grace_period_s
        self.terminated = True


class _ExitedHandle:
    returncode = 0
    pid = None


class _ResumableStrategy(BaseExecutionStrategy):
    def supports_session_continuation(self) -> bool:
        return True

    def supports_completion_enforcement(self) -> bool:
        return True

    def classify_exit(self, *args: object, **kwargs: object) -> AgentExecutionState:
        del args, kwargs
        return AgentExecutionState.RESUMABLE_CONTINUE


def _watchdog(clock: FakeClock) -> IdleWatchdog:
    watchdog = IdleWatchdog(TimeoutPolicy(idle_timeout_seconds=300.0), clock)
    watchdog.record_invocation_start()
    return watchdog


def _lifecycle_only_watchdog(clock: FakeClock) -> IdleWatchdog:
    watchdog = _watchdog(clock)
    for _ in range(4):
        clock.advance(10.0)
        watchdog.record_lifecycle_activity()
    return watchdog


def _completion_options(elapsed_seconds: float) -> CompletionCheckOptions:
    return CompletionCheckOptions(
        execution_strategy=_ResumableStrategy(),
        workspace_path=Path("synthetic://no-llm-activity"),
        policy=TimeoutPolicy(
            idle_timeout_seconds=None,
            parent_exit_grace_seconds=0.0,
            descendant_wait_timeout_seconds=0.0,
        ),
        evaluate_completion_fn=lambda *args, **kwargs: CompletionSignals(False, False, ()),
        elapsed_seconds=elapsed_seconds,
        has_meaningful_output=False,
    )


def test_live_timer_classifies_lifecycle_only_run_as_no_llm_activity() -> None:
    clock = FakeClock(start=0.0)
    handle = _LiveHandle()
    watchdog = _lifecycle_only_watchdog(clock)

    assert watchdog.has_any_output() is True

    with pytest.raises(BrokenAgentExitError) as excinfo:
        check_broken_agent_timer(handle, watchdog, "claude")

    assert handle.terminated is True
    assert excinfo.value.reason == "no_llm_activity"


def _assert_completion_gate_classifies_lifecycle_only_run(
    reader: object,
) -> None:
    clock = FakeClock(start=0.0)
    watchdog = _lifecycle_only_watchdog(clock)
    reader_with_watchdog = SimpleNamespace(_watchdog=watchdog)
    _, _, elapsed_seconds, _ = collect_r7_diagnostic_fields(
        reader=reader_with_watchdog,
        clock=clock,
        parsed_output=deque(["session-id: test", "thinking: checking credentials"]),
    )

    assert elapsed_seconds is not None
    assert elapsed_seconds >= BROKEN_AGENT_OUTPUT_GRACE_SECONDS

    with pytest.raises(BrokenAgentExitError) as excinfo:
        check_process_result(
            _ExitedHandle(),
            "claude",
            ["session-id: test", "thinking: checking credentials"],
            _completion_options(elapsed_seconds),
            _clock=clock,
        )

    assert excinfo.value.reason == "no_llm_activity"


def test_subprocess_completion_classifies_lifecycle_only_run_as_no_llm_activity() -> None:
    _assert_completion_gate_classifies_lifecycle_only_run(SimpleNamespace())


def test_pty_completion_classifies_lifecycle_only_run_as_no_llm_activity() -> None:
    _assert_completion_gate_classifies_lifecycle_only_run(SimpleNamespace())
