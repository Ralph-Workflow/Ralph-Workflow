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
    OpenCodeResumableExitError,
    check_process_result,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.recovery.failure_classifier import FailureClassifier

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


def test_completion_gate_regression_prioritizes_credentials_over_no_llm_activity() -> None:
    """DA-003: credential output wins even when the watchdog saw no LLM activity."""
    with pytest.raises(BrokenAgentExitError) as excinfo:
        check_process_result(
            _Handle(),
            "opencode",
            ["openai_api_key: missing"],
            _options(
                elapsed_seconds=2.0,
                input_prompt="implement the change",
                has_meaningful_output=False,
            ),
            _clock=FakeClock(),
        )

    assert excinfo.value.reason == "no_output"


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


def test_completion_gate_substantial_output_marked_no_meaningful_still_resumable() -> None:
    """S-2: substantial no-LLM-activity output should fall through to resumable flow."""
    output_lines = [
        "event-0: " + ("x" * 70),
        "event-1: " + ("y" * 70),
        "event-2: " + ("z" * 70),
        "event-3: " + ("a" * 70),
    ]

    assert len([line for line in output_lines if line.strip()]) > 2
    assert sum(len(line.encode("utf-8")) for line in output_lines) > 256

    with pytest.raises(Exception) as excinfo:
        check_process_result(
            _Handle(),
            "opencode",
            output_lines,
            _options(
                elapsed_seconds=2.0,
                input_prompt="implement the change",
                has_meaningful_output=False,
            ),
            _clock=FakeClock(),
        )

    classified = FailureClassifier().classify(
        excinfo.value,
        phase="development",
        agent="opencode",
        connectivity_state="online",
    )

    assert isinstance(excinfo.value, OpenCodeResumableExitError)
    assert classified.is_unavailable is False
    assert classified.unavailability_reason is None


def test_completion_gate_missing_plan_receipt_with_substantial_output_is_not_provider_failure() -> (
    None
):
    """Reported regression: artifact-submission failure is not provider silence.

    Reproduces the reported pi planning run end to end at the completion
    gate: the agent streamed a large transcript past the grace window, the
    watchdog never saw meaningful LLM output, and neither the plan receipt
    nor the completion sentinel exists. The gate must raise the resumable
    completion-evidence error (same-session technical retry), never
    ``BrokenAgentExitError`` (provider-unavailability fallover).
    """
    output_lines = [
        '{"type":"grep_files","line":' + str(index) + "," + '"text":"' + "t" * 90 + '"}'
        for index in range(20)
    ]
    assert len([line for line in output_lines if line.strip()]) > 2
    assert sum(len(line.encode("utf-8")) for line in output_lines) > 256

    with pytest.raises(OpenCodeResumableExitError) as excinfo:
        check_process_result(
            _Handle(),
            "pi/minimax/MiniMax-3",
            output_lines,
            _options(
                elapsed_seconds=40.0,
                input_prompt="produce the plan",
                has_meaningful_output=False,
            ),
            _clock=FakeClock(),
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


def test_completion_gate_substantial_output_mentioning_credentials_is_not_broken_agent() -> None:
    """Reported regression: a large transcript that mentions credentials is not provider silence.

    Reproduces the false positive where an agent streamed a real working
    transcript (tool calls, grep results) whose lines happened to contain a
    credentials-flavoured substring -- the echoed master prompt, the prior
    retry error block (``check credentials or provider availability``), or
    source code under discussion -- and the completion gate misclassified
    the run as ``BrokenAgentExitError: no meaningful LLM output`` solely
    because the required plan artifact was never submitted. A substantial
    transcript must follow the resumable missing-artifact path instead.
    """
    output_lines = [
        '{"type":"grep_files","line":' + str(index) + "," + '"text":"' + "t" * 90 + '"}'
        for index in range(20)
    ] + [
        "BrokenAgentExitError: agent appears broken: no meaningful LLM output; "
        "check credentials or provider availability",
    ]
    assert sum(len(line.encode("utf-8")) for line in output_lines) > 256

    with pytest.raises(OpenCodeResumableExitError) as excinfo:
        check_process_result(
            _Handle(),
            "pi/minimax/MiniMax-3",
            output_lines,
            _options(
                elapsed_seconds=40.0,
                input_prompt="produce the plan",
                has_meaningful_output=False,
            ),
            _clock=FakeClock(),
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


def test_completion_gate_fast_credentials_with_small_output_still_broken() -> None:
    """A structurally small credential failure keeps the fast broken-agent fallover."""
    with pytest.raises(BrokenAgentExitError) as excinfo:
        check_process_result(
            _Handle(),
            "opencode",
            ["error: authentication failed: invalid key"],
            _options(elapsed_seconds=2.0, input_prompt="implement the change"),
            _clock=FakeClock(),
        )

    assert excinfo.value.reason == "no_output"
