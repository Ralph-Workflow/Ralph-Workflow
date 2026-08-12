"""Completion-gate regression coverage for broken agents."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import AgentExecutionState, BaseExecutionStrategy
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import BrokenAgentExitError
from ralph.agents.invoke._completion import _check_process_result, _CompletionCheckOptions
from ralph.agents.timeout_clock import FakeClock

if TYPE_CHECKING:
    from ralph.agents.execution_state._live_descendant_handle import _LiveDescendantHandle
    from ralph.process.liveness import LivenessProbe


class _ResumableStrategy(BaseExecutionStrategy):
    def supports_session_continuation(self) -> bool:
        return True

    def supports_completion_enforcement(self) -> bool:
        return True

    def classify_exit(
        self,
        handle: _LiveDescendantHandle,
        completion_signals: CompletionSignals,
        liveness_probe: LivenessProbe | None = None,
    ) -> AgentExecutionState:
        del handle, completion_signals, liveness_probe
        return AgentExecutionState.RESUMABLE_CONTINUE


class _Handle:
    returncode = 0
    pid = None


def _completion_signals(*args: object, **kwargs: object) -> CompletionSignals:
    del args, kwargs
    return CompletionSignals(False, False, ())


def _options(*, elapsed_seconds: float, input_prompt: str) -> _CompletionCheckOptions:
    return _CompletionCheckOptions(
        execution_strategy=_ResumableStrategy(),
        workspace_path=Path("synthetic://broken-agent"),
        policy=TimeoutPolicy(
            idle_timeout_seconds=None,
            parent_exit_grace_seconds=0.0,
            descendant_wait_timeout_seconds=0.0,
        ),
        evaluate_completion_fn=_completion_signals,
        elapsed_seconds=elapsed_seconds,
        input_prompt=input_prompt,
    )


def test_completion_gate_classifies_empty_long_running_exit_as_broken_agent() -> None:
    with pytest.raises(BrokenAgentExitError) as excinfo:
        _check_process_result(
            _Handle(),
            "claude",
            [],
            _options(elapsed_seconds=35.0, input_prompt="implement the change"),
            _clock=FakeClock(),
        )

    assert excinfo.value.reason == "no_output"
    assert excinfo.value.elapsed_seconds == 35.0


def test_completion_gate_classifies_all_prompt_echo_output_as_broken_agent() -> None:
    prompt = "implement the change"
    with pytest.raises(BrokenAgentExitError) as excinfo:
        _check_process_result(
            _Handle(),
            "claude",
            [prompt, f"Input prompt: {prompt}"],
            _options(elapsed_seconds=35.0, input_prompt=prompt),
            _clock=FakeClock(),
        )

    assert excinfo.value.reason == "prompt_echo"


def test_completion_gate_keeps_mixed_output_resumable() -> None:
    prompt = "implement the change"
    with pytest.raises(Exception) as excinfo:
        _check_process_result(
            _Handle(),
            "claude",
            [prompt, "thinking: checking the implementation"],
            _options(elapsed_seconds=35.0, input_prompt=prompt),
            _clock=FakeClock(),
        )

    assert not isinstance(excinfo.value, BrokenAgentExitError)
