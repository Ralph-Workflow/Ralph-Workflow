"""Black-box tests for AGY incomplete-exit bounded recovery.

An AGY headless run that exits rc=0 without the required completion
evidence (the durable ``declare_complete`` sentinel, or a required
artifact receipt) raises the typed ``AgyIncompleteExitError`` instead of
a plain ``AgentInvocationError``. The typed error:

  * classifies deterministically as ``FailureCategory.AGENT`` (never
    ``AMBIGUOUS``), BEFORE the broader ``AgentInvocationError`` branch;
  * maps to a canonical retry reason so the recovery layer issues ONE
    bounded automatic reprompt (a fresh invocation carrying the original
    task plus an explicit completion instruction);
  * is bounded to exactly one reprompt per invocation at BOTH
    enforcement points (``build_agent_recovery_plan`` and the
    ``run_with_direct_mcp_recovery`` loop) -- never an unbounded loop;
  * is scoped to strategies that declare
    ``supports_incomplete_exit_reprompt()`` (AGY) -- other
    completion-enforcing transports (e.g. Cursor) keep the plain
    ``AgentInvocationError`` behavior.

AGY exposes no stable wire-level "waiting for user input" signal, so
recovery is limited to the objective condition "process exited without
required completion evidence"; see ``docs/sphinx/recovery.md``.

No real subprocesses, no real wall clock, no network.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from ralph.agents.execution_state import strategy_for_transport
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    AgyIncompleteExitError,
    CompletionCheckOptions,
    OpenCodeResumableExitError,
    check_process_result,
)
from ralph.agents.invoke._direct_mcp_recovery import run_with_direct_mcp_recovery
from ralph.config.enums import AgentTransport
from ralph.pipeline.effect_executor import AgentRecoveryInput, build_agent_recovery_plan
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.retryable_failure import retryable_agent_failure_reason
from ralph.recovery.classifier import FailureCategory, FailureClassifier


def _check_clean_exit_without_evidence(
    tmp_path: Path,
    transport: AgentTransport,
    agent_name: str,
) -> None:
    """Run ``check_process_result`` for a rc=0 exit that left no completion evidence."""
    fake_handle = types.SimpleNamespace(returncode=0)
    check_process_result(
        fake_handle,
        agent_name,
        [],
        CompletionCheckOptions(
            execution_strategy=strategy_for_transport(transport),
            workspace_path=tmp_path,
            policy=TimeoutPolicy(
                idle_timeout_seconds=None,
                parent_exit_grace_seconds=0.0,
            ),
            completion_run_id=agent_name,
            # Isolate AGY's empty-output diagnostic from the host-global
            # cli.log (same reasoning as tests/test_agy_runner_no_retry.py).
            agy_cli_log_path=tmp_path / "cli.log",
        ),
    )


def _make_effect(prompt_file: str) -> InvokeAgentEffect:
    return InvokeAgentEffect(agent_name="agy", phase="development", prompt_file=prompt_file)


def _make_recovery_input(
    exc: Exception,
    tmp_path: Path,
    *,
    completion_reprompt_used: bool = False,
) -> AgentRecoveryInput:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("implement the task", encoding="utf-8")
    return AgentRecoveryInput(
        exc=exc,
        attempt_index=0,
        max_recovery_attempts=3,
        effect=_make_effect(str(prompt_file)),
        workspace_root=tmp_path,
        raw_output=[],
        rendered_output=[],
        extracted_session_id=None,
        inactivity_error_type=AgentInactivityTimeoutError,
        completion_reprompt_used=completion_reprompt_used,
    )


def test_agy_clean_exit_without_evidence_raises_typed_error(tmp_path: Path) -> None:
    """AGY rc=0 without completion evidence raises ``AgyIncompleteExitError``."""
    with pytest.raises(AgyIncompleteExitError) as excinfo:
        _check_clean_exit_without_evidence(tmp_path, AgentTransport.AGY, "agy")
    # The typed error remains an AgentInvocationError for every legacy caller.
    assert isinstance(excinfo.value, AgentInvocationError)


def test_agy_incomplete_exit_message_is_actionable(tmp_path: Path) -> None:
    """The terminal error tells the operator what evidence was missing and what to check."""
    with pytest.raises(AgyIncompleteExitError) as excinfo:
        _check_clean_exit_without_evidence(tmp_path, AgentTransport.AGY, "agy")
    message = str(excinfo.value)
    assert "completion sentinel missing" in message
    assert "declare_complete" in message
    assert "ralph_submit_md_artifact" in message


def test_cursor_clean_exit_without_evidence_keeps_plain_error(tmp_path: Path) -> None:
    """Scoping guard: non-AGY completion-enforcing transports are unaffected."""
    with pytest.raises(AgentInvocationError) as excinfo:
        _check_clean_exit_without_evidence(tmp_path, AgentTransport.CURSOR, "agent")
    assert not isinstance(excinfo.value, AgyIncompleteExitError)


def test_agy_incomplete_exit_classified_as_agent_failure() -> None:
    """``AgyIncompleteExitError`` classifies as AGENT (never AMBIGUOUS)."""
    classifier = FailureClassifier()
    exc = AgyIncompleteExitError("agy")

    failure = classifier.classify(exc, phase="development", agent="agy")

    assert failure.category == FailureCategory.AGENT, (
        f"AgyIncompleteExitError MUST classify as AGENT; got {failure.category!r}"
    )
    assert failure.counts_against_budget is True
    assert failure.reset_session is False


def test_agy_incomplete_exit_has_retryable_reason() -> None:
    """The recovery layer sees a canonical retry reason for the typed error."""
    reason = retryable_agent_failure_reason(
        AgyIncompleteExitError("agy"), AgentInactivityTimeoutError
    )
    assert reason == "the agent exited without required completion evidence"


def test_recovery_plan_is_fresh_with_completion_instruction(tmp_path: Path) -> None:
    """The reprompt is a FRESH invocation: original task plus completion instruction.

    AGY does not demonstrably support resumable sessions (the continuation
    probes did not expose session identity), so the plan must never resume.
    """
    plan = build_agent_recovery_plan(
        _make_recovery_input(AgyIncompleteExitError("agy"), tmp_path)
    )

    assert plan is not None, "expected a recovery plan for the first incomplete exit"
    assert plan.recovery_action == "fresh"
    text = Path(plan.prompt_file).read_text(encoding="utf-8")
    assert "ORIGINAL TASK PROMPT:" in text
    assert "implement the task" in text
    assert "COMPLETION RECOVERY INSTRUCTION" in text
    assert "ralph_submit_md_artifact" in text
    assert "declare_complete" in text


def test_recovery_plan_none_when_completion_reprompt_already_used(tmp_path: Path) -> None:
    """Bounded: the second incomplete exit in the same invocation gets no plan."""
    plan = build_agent_recovery_plan(
        _make_recovery_input(
            AgyIncompleteExitError("agy"),
            tmp_path,
            completion_reprompt_used=True,
        )
    )

    assert plan is None, (
        "the bounded recovery MUST NOT issue a second reprompt for the same invocation"
    )


def test_direct_mcp_recovery_bounds_agy_reprompt_to_one() -> None:
    """The retry driver runs the attempt exactly twice for repeated incomplete exits."""
    attempts = [0]

    def attempt_fn(
        retry_session_id: str | None,
        capture_session_id: object,
    ) -> object:
        del retry_session_id, capture_session_id
        attempts[0] += 1
        raise AgyIncompleteExitError("agy")

    with pytest.raises(AgyIncompleteExitError):
        run_with_direct_mcp_recovery(
            attempt_fn,
            max_retries=10,
            reset_tool_registry=lambda: None,
            retry_resumable_exit=True,
        )

    assert attempts[0] == 2, (
        f"expected exactly 1 bounded reprompt (2 attempts); got {attempts[0]} attempts"
    )


def test_direct_mcp_recovery_bound_is_scoped_to_agy_incomplete_exit() -> None:
    """Other retryable exits keep the ordinary max_retries budget."""
    attempts = [0]

    def attempt_fn(
        retry_session_id: str | None,
        capture_session_id: object,
    ) -> object:
        del retry_session_id, capture_session_id
        attempts[0] += 1
        raise OpenCodeResumableExitError("opencode", session_id=None)

    with pytest.raises(OpenCodeResumableExitError):
        run_with_direct_mcp_recovery(
            attempt_fn,
            max_retries=2,
            reset_tool_registry=lambda: None,
            retry_resumable_exit=True,
        )

    assert attempts[0] == 3, (
        f"non-AGY failures keep max_retries=2 (3 attempts); got {attempts[0]}"
    )
