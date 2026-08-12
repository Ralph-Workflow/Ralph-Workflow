"""Completion-gate regression coverage for broken agents."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import AgentExecutionState, BaseExecutionStrategy
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    AgentInvocationError,
    BrokenAgentExitError,
    CompletionCheckOptions,
    check_process_result,
)
from ralph.agents.timeout_clock import FakeClock

if TYPE_CHECKING:
    from ralph.agents.execution_state import LiveDescendantHandle
    from ralph.process.liveness import LivenessProbe


class _ResumableStrategy(BaseExecutionStrategy):
    def supports_session_continuation(self) -> bool:
        return True

    def supports_completion_enforcement(self) -> bool:
        return True

    def classify_exit(
        self,
        handle: LiveDescendantHandle,
        completion_signals: CompletionSignals,
        liveness_probe: LivenessProbe | None = None,
    ) -> AgentExecutionState:
        del handle, completion_signals, liveness_probe
        return AgentExecutionState.RESUMABLE_CONTINUE


class _Handle:
    returncode = 0
    pid = None


class _HandleWithStderr(_Handle):
    def __init__(self, *, returncode: int, stderr_text: str) -> None:
        self.returncode = returncode
        self.stderr = StringIO(stderr_text)


def _completion_signals(*args: object, **kwargs: object) -> CompletionSignals:
    del args, kwargs
    return CompletionSignals(False, False, ())


def _options(
    *,
    elapsed_seconds: float,
    input_prompt: str | None,
    has_meaningful_output: bool | None = None,
) -> CompletionCheckOptions:
    return CompletionCheckOptions(
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
        has_meaningful_output=has_meaningful_output,
    )


def test_completion_gate_regression_classifies_fast_empty_exit_as_broken_agent() -> None:
    """S-2: a clean exit with no output must fall over before the grace window."""
    with pytest.raises(BrokenAgentExitError) as excinfo:
        check_process_result(
            _Handle(),
            "claude",
            [],
            _options(elapsed_seconds=2.0, input_prompt="implement the change"),
            _clock=FakeClock(),
        )

    assert excinfo.value.reason == "no_output"
    assert excinfo.value.elapsed_seconds == 2.0


def test_completion_gate_classifies_all_prompt_echo_output_as_broken_agent() -> None:
    prompt = "implement the change"
    with pytest.raises(BrokenAgentExitError) as excinfo:
        check_process_result(
            _Handle(),
            "claude",
            [prompt, f"Input prompt: {prompt}"],
            _options(elapsed_seconds=35.0, input_prompt=prompt),
            _clock=FakeClock(),
        )

    assert excinfo.value.reason == "prompt_echo"


def test_completion_gate_regression_classifies_fast_credentials_in_output_as_broken_agent() -> None:
    """S-5(a): a credential marker in clean-exit output must bypass the grace window."""
    with pytest.raises(BrokenAgentExitError) as excinfo:
        check_process_result(
            _Handle(),
            "opencode",
            ["openai_api_key: missing"],
            _options(elapsed_seconds=2.0, input_prompt="implement the change"),
            _clock=FakeClock(),
        )

    assert excinfo.value.reason == "no_output"
    assert excinfo.value.elapsed_seconds == 2.0


def test_completion_gate_regression_classifies_fast_credentials_in_stderr_as_broken_agent() -> None:
    """S-5(b): a clean-exit stderr credential marker must bypass the grace window."""
    with pytest.raises(BrokenAgentExitError) as excinfo:
        check_process_result(
            _HandleWithStderr(returncode=0, stderr_text="401 unauthorized"),
            "opencode",
            ["harness completed"],
            _options(elapsed_seconds=2.0, input_prompt="implement the change"),
            _clock=FakeClock(),
        )

    assert excinfo.value.reason == "no_output"


def test_completion_gate_regression_classifies_fast_nonzero_credentials_as_broken_agent() -> None:
    """S-5(c): a fast nonzero credential failure must fall over rather than retry."""
    with pytest.raises(BrokenAgentExitError) as excinfo:
        check_process_result(
            _HandleWithStderr(returncode=1, stderr_text="403 forbidden"),
            "opencode",
            [],
            _options(elapsed_seconds=2.0, input_prompt="implement the change"),
        )

    assert excinfo.value.reason == "no_output"
    assert excinfo.value.elapsed_seconds == 2.0


def test_completion_gate_regression_keeps_late_nonzero_credentials_recoverable() -> None:
    """S-5(d): a late credential loss remains a generic recoverable failure."""
    with pytest.raises(AgentInvocationError) as excinfo:
        check_process_result(
            _HandleWithStderr(returncode=1, stderr_text="403 forbidden"),
            "opencode",
            [],
            _options(elapsed_seconds=35.0, input_prompt="implement the change"),
        )

    assert not isinstance(excinfo.value, BrokenAgentExitError)


def test_completion_gate_regression_classifies_fast_no_llm_activity_as_broken_agent() -> None:
    """S-5(e): watchdog-confirmed harness-only output must bypass the grace window."""
    with pytest.raises(BrokenAgentExitError) as excinfo:
        check_process_result(
            _Handle(),
            "opencode",
            ["Input prompt: implement the change"],
            _options(
                elapsed_seconds=2.0,
                input_prompt=None,
                has_meaningful_output=False,
            ),
            _clock=FakeClock(),
        )

    assert excinfo.value.reason == "no_llm_activity"


def test_completion_gate_keeps_mixed_output_resumable() -> None:
    prompt = "implement the change"
    with pytest.raises(Exception) as excinfo:
        check_process_result(
            _Handle(),
            "claude",
            [prompt, "thinking: checking the implementation"],
            _options(elapsed_seconds=35.0, input_prompt=prompt),
            _clock=FakeClock(),
        )

    assert not isinstance(excinfo.value, BrokenAgentExitError)
