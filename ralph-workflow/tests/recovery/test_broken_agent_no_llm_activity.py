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
    OpenCodeResumableExitError,
    check_broken_agent_timer,
    check_process_result,
    collect_r7_diagnostic_fields,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.process.manager import ManagedProcess
from ralph.recovery.failure_classifier import FailureClassifier
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
    # Two harness-only lifecycle frames keep the observed stream inside the
    # shared structurally-small envelope (<= 2 nonblank output signals), so
    # this remains a genuinely tiny harness-only run. The clock advances far
    # past the broken-agent grace window either way.
    for _ in range(2):
        clock.advance(10.0)
        watchdog.record_lifecycle_activity()
    return watchdog


def _substantial_unclassified_watchdog(clock: FakeClock) -> IdleWatchdog:
    """Reproduce the reported shape: many output lines, none meaningful.

    The run emitted dozens of tool-result/status lines the watchdog could
    not classify as meaningful LLM output, yet the agent clearly produced
    substantial output before exiting without the required plan receipt.
    """
    watchdog = _watchdog(clock)
    for index in range(40):
        clock.advance(0.5)
        watchdog.record_any_output(byte_size=len(f"tool-result-{index}: " + "x" * 100))
    clock.advance(BROKEN_AGENT_OUTPUT_GRACE_SECONDS)
    return watchdog


class _ExitedPollingHandle:
    """Dead-process seam: ``poll`` reports the exit, like a real handle."""

    returncode = 0
    pid = None

    def poll(self) -> int:
        return 0

    def terminate(self, grace_period_s: float | None = None) -> None:
        del grace_period_s


def _completion_options(
    elapsed_seconds: float, has_meaningful_output: bool
) -> CompletionCheckOptions:
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
        has_meaningful_output=has_meaningful_output,
    )


def test_live_timer_classifies_lifecycle_only_run_as_no_llm_activity() -> None:
    clock = FakeClock(start=0.0)
    handle = _LiveHandle()
    watchdog = _lifecycle_only_watchdog(clock)

    assert watchdog.has_any_output() is True
    assert watchdog.observed_output_is_structurally_small() is True

    with pytest.raises(BrokenAgentExitError) as excinfo:
        check_broken_agent_timer(handle, watchdog, "claude")

    assert handle.terminated is True
    assert excinfo.value.reason == "no_llm_activity"


def test_live_timer_spares_substantial_unclassified_output() -> None:
    """Reported regression: substantial non-meaningful output is not silence.

    The MiniMax-3 planning run emitted hundreds of output lines (grep tool
    results, status frames) yet never submitted the required plan artifact.
    The live broken-agent timer must NOT terminate the process or mark the
    provider broken; the completion gate owns that classification once the
    agent exits on its own.
    """
    clock = FakeClock(start=0.0)
    handle = _LiveHandle()
    watchdog = _substantial_unclassified_watchdog(clock)

    assert watchdog.has_any_output() is True
    assert watchdog.has_meaningful_output() is False
    assert watchdog.observed_output_is_structurally_small() is False

    check_broken_agent_timer(handle, watchdog, "pi/minimax/MiniMax-3")

    assert handle.terminated is False


def test_live_timer_spares_substantial_output_after_process_exit() -> None:
    """A dead process with substantial observed output is not provider silence.

    The dead-process fast-fail branch may only fire for structurally small
    output; substantial output must flow to the completion gate so the run
    is retried as a missing-artifact failure instead of failing over.
    """
    clock = FakeClock(start=0.0)
    watchdog = _substantial_unclassified_watchdog(clock)

    assert watchdog.observed_output_is_structurally_small() is False

    check_broken_agent_timer(_ExitedPollingHandle(), watchdog, "pi/minimax/MiniMax-3")


def _assert_completion_gate_classifies_lifecycle_only_run(
    reader: object,
) -> None:
    clock = FakeClock(start=0.0)
    watchdog = _lifecycle_only_watchdog(clock)
    reader_with_watchdog = SimpleNamespace(_watchdog=watchdog)
    _, _, elapsed_seconds, _ = collect_r7_diagnostic_fields(
        reader=reader_with_watchdog,
        clock=clock,
        parsed_output=deque(["session-id: test", "thinking: checking connection"]),
    )

    assert elapsed_seconds is not None
    assert elapsed_seconds >= BROKEN_AGENT_OUTPUT_GRACE_SECONDS
    assert watchdog.has_any_output() is True

    with pytest.raises(BrokenAgentExitError) as excinfo:
        check_process_result(
            _ExitedHandle(),
            "claude",
            ["session-id: test", "thinking: checking connection"],
            _completion_options(elapsed_seconds, bool(watchdog.has_meaningful_output())),
            _clock=clock,
        )

    assert excinfo.value.reason == "no_llm_activity"


def test_subprocess_completion_classifies_lifecycle_only_run_as_no_llm_activity() -> None:
    _assert_completion_gate_classifies_lifecycle_only_run(SimpleNamespace())


def test_pty_completion_classifies_lifecycle_only_run_as_no_llm_activity() -> None:
    _assert_completion_gate_classifies_lifecycle_only_run(SimpleNamespace())


def test_completion_gate_pi_substantial_output_missing_plan_receipt_is_resumable() -> None:
    """Reported regression: exit-0 with substantial output but no plan receipt.

    The planning run streamed a large transcript (tool results, status
    frames), never called the plan submission endpoint, and exited 0. That
    is a failure to submit the required artifact, not provider
    unavailability: the completion gate must raise the resumable
    completion-evidence error, and the failure classifier must not mark
    the provider unavailable.
    """
    output_lines = [
        '{"type":"tool_result","text":"chunk ' + str(index) + ": " + "t" * 90 + '"}'
        for index in range(20)
    ]
    assert sum(len(line.encode("utf-8")) for line in output_lines) > 256

    with pytest.raises(OpenCodeResumableExitError) as excinfo:
        check_process_result(
            _ExitedHandle(),
            "pi/minimax/MiniMax-3",
            output_lines,
            _completion_options(
                elapsed_seconds=BROKEN_AGENT_OUTPUT_GRACE_SECONDS + 28.0,
                has_meaningful_output=False,
            ),
            _clock=FakeClock(start=0.0),
        )

    assert not isinstance(excinfo.value, BrokenAgentExitError)
    classified = FailureClassifier().classify(
        excinfo.value,
        phase="planning",
        agent="pi/minimax/MiniMax-3",
        connectivity_state="online",
    )
    assert classified.is_unavailable is False
    assert classified.unavailability_reason is None
