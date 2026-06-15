"""Process completion checking and post-exit waiting logic."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import IO, TYPE_CHECKING, cast

from loguru import logger

from ralph.agents.completion_signals import _check_completion_sentinel, evaluate_completion
from ralph.agents.execution_state import (
    AgentExecutionState,
    GenericExecutionStrategy,
    OpenCodeExecutionStrategy,
)
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._direct_mcp_recovery import summarize_retry_failure_evidence
from ralph.agents.invoke._errors import AgentInvocationError, OpenCodeResumableExitError
from ralph.agents.invoke._session import _bounded_output_lines, extract_transport_session_id
from ralph.agents.post_exit_watchdog import PostExitVerdict, PostExitWatchdog
from ralph.agents.timeout_clock import Clock, SystemClock
from ralph.mcp.protocol.env import MCP_RUN_ID_ENV
from ralph.pipeline.retryable_failure import retryable_agent_failure_reason
from ralph.process.liveness import DefaultLivenessProbe, LivenessProbe
from ralph.process.teardown import teardown_subtree
from ralph.recovery.failure_classifier import FailureClassifier

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.agents.invoke._agent_run_ctx import _EvalCompletionFn
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.process.manager import ManagedProcess, ManagedPtyProcess


def completion_run_id_from_extra_env(extra_env: dict[str, str] | None) -> str | None:
    """Resolve the gate's run identity from the agent's MCP_RUN_ID_ENV variable.

    The launcher sets this env var to the MCP session's run_id (the same value the
    artifact handler stamps receipts with), so resolving it here lets the gate
    correlate a receipt to the submission that produced it — for subprocess
    agents that report no usable transport session id.
    """
    if extra_env is None:
        return None
    return extra_env.get(str(MCP_RUN_ID_ENV)) or None


def _completion_run_id(opts: _CompletionCheckOptions) -> str | None:
    """The run identity used to correlate completion receipts and the sentinel.

    Both the submission handler (which writes receipts keyed by the MCP session's
    run_id) and the gate must agree on this value; it is the completion_run_id
    when threaded, else the transport session id captured from agent output.
    """
    return opts.completion_run_id or opts.captured_session_id


def _teardown_subtree_if_pid_available(handle: object) -> None:
    """Best-effort subtree teardown when the handle exposes a PID.

    Test fakes may not implement ``pid``; this helper ignores them so
    unit tests stay isolated from real process signals.
    """
    pid = cast("int | None", getattr(handle, "pid", None))
    if pid is not None:
        teardown_subtree(pid)


@dataclass(frozen=True)
class _CompletionCheckOptions:
    execution_strategy: GenericExecutionStrategy | OpenCodeExecutionStrategy | None = None
    workspace_path: Path | None = None
    liveness_probe: LivenessProbe | None = None
    policy: TimeoutPolicy = field(default_factory=lambda: TimeoutPolicy(idle_timeout_seconds=None))
    required_artifact: RequiredArtifact | None = None
    explicit_completion_seen: bool = False
    captured_session_id: str | None = None
    completion_run_id: str | None = None
    evaluate_completion_fn: _EvalCompletionFn | None = None
    _sentinel_check_fn: Callable[[Path, str | None], bool] | None = field(default=None)


def _wait_for_completion_grace(
    handle: ManagedProcess | ManagedPtyProcess,
    opts: _CompletionCheckOptions,
    parsed_output: list[str],
    *,
    clock: Clock | None = None,
) -> AgentExecutionState:
    """Wait up to policy.parent_exit_grace_seconds for completion signals or children to appear.

    Polls evaluate_completion + classify_exit at policy.descendant_wait_poll_seconds intervals.
    Returns:
      TERMINAL_COMPLETE if completion signals appear during the grace window.
      WAITING_ON_CHILD if children appear (caller must escalate to descendant wait).
      RESUMABLE_CONTINUE if grace deadline elapses with no signals and no children.
    """
    assert opts.workspace_path is not None
    workspace_path = opts.workspace_path
    execution_strategy = opts.execution_strategy
    assert execution_strategy is not None

    effective_clock: Clock = clock or SystemClock()
    probe = opts.liveness_probe or DefaultLivenessProbe()

    _eval_fn = (
        opts.evaluate_completion_fn
        if opts.evaluate_completion_fn is not None
        else evaluate_completion
    )

    def classify_exit_state() -> AgentExecutionState:
        signals = _eval_fn(
            workspace_path,
            _bounded_output_lines(
                parsed_output,
                explicit_completion_seen=opts.explicit_completion_seen,
            ),
            required_artifact=opts.required_artifact,
            run_id=_completion_run_id(opts),
        )
        return execution_strategy.classify_exit(handle, signals, liveness_probe=probe)

    post_exit = PostExitWatchdog(opts.policy, effective_clock)
    verdict = post_exit.wait_parent_exit_grace(classify_exit_state)
    _teardown_subtree_if_pid_available(handle)
    if verdict == PostExitVerdict.SIGNALS_PRESENT:
        return AgentExecutionState.TERMINAL_COMPLETE
    if verdict == PostExitVerdict.CHILDREN_ACTIVE:
        return AgentExecutionState.WAITING_ON_CHILD
    return AgentExecutionState.RESUMABLE_CONTINUE


def _wait_for_descendants_then_recheck(
    handle: ManagedProcess | ManagedPtyProcess,
    opts: _CompletionCheckOptions,
    parsed_output: list[str],
    *,
    clock: Clock | None = None,
) -> AgentExecutionState:
    """Wait for descendant processes to finish, then re-evaluate completion signals.

    Polls the execution strategy's classify_exit at policy.descendant_wait_poll_seconds
    intervals until either the tree is quiet (state != WAITING_ON_CHILD) or the deadline
    elapses. This allows artifacts written by background subagents to become visible before
    OpenCodeResumableExitError is raised.

    Args:
        handle: Completed parent process handle.
        opts: Completion check options including liveness_probe and policy.
        parsed_output: Raw NDJSON output lines from the agent.
        clock: Injectable Clock; defaults to SystemClock.

    Returns:
        TERMINAL_COMPLETE if tree quiessed and completion signals present.
        RESUMABLE_CONTINUE if deadline elapsed with children still alive (fallback to
        retry rather than silent success). WAITING_ON_CHILD is only returned during
        the active polling loop, never after deadline.
    """
    assert opts.workspace_path is not None
    workspace_path = opts.workspace_path
    execution_strategy = opts.execution_strategy
    assert execution_strategy is not None

    effective_clock: Clock = clock or SystemClock()
    probe = opts.liveness_probe or DefaultLivenessProbe()

    _eval_fn = (
        opts.evaluate_completion_fn
        if opts.evaluate_completion_fn is not None
        else evaluate_completion
    )

    def classify_exit_state() -> AgentExecutionState:
        signals = _eval_fn(
            workspace_path,
            _bounded_output_lines(
                parsed_output,
                explicit_completion_seen=opts.explicit_completion_seen,
            ),
            required_artifact=opts.required_artifact,
            run_id=_completion_run_id(opts),
        )
        return execution_strategy.classify_exit(handle, signals, liveness_probe=probe)

    post_exit = PostExitWatchdog(opts.policy, effective_clock)
    verdict = post_exit.wait_descendant_quiesce(classify_exit_state)
    _teardown_subtree_if_pid_available(handle)
    if verdict == PostExitVerdict.SIGNALS_PRESENT:
        return AgentExecutionState.TERMINAL_COMPLETE
    if verdict == PostExitVerdict.QUIESCED_NO_SIGNALS:
        return AgentExecutionState.RESUMABLE_CONTINUE
    return AgentExecutionState.RESUMABLE_CONTINUE


def _check_process_result(
    handle: ManagedProcess | ManagedPtyProcess,
    agent_name: str,
    parsed_output: list[str] | None = None,
    check_options: _CompletionCheckOptions | None = None,
    *,
    _clock: Clock | None = None,
) -> None:
    """Check subprocess return code and raise error if non-zero.

    For session-continuing agents, exit 0 without required completion evidence raises
    OpenCodeResumableExitError so the runner can continue the same session.
    When the process exits but child agents are still running, this function
    waits up to policy.descendant_wait_timeout_seconds for the tree to quiesce
    before re-evaluating completion signals.

    Args:
        handle: Completed managed process.
        agent_name: Name of the agent.
        _clock: Injectable Clock for testing; production callers omit this.

    Raises:
        AgentInvocationError: If process exited with non-zero code.
        OpenCodeResumableExitError: If the agent session exited without required
            completion evidence and no child agents are still running.
    """
    returncode = int(handle.returncode or 0)
    if returncode != 0:
        stderr_pipe = cast("IO[str] | None", getattr(handle, "stderr", None))
        stderr = stderr_pipe.read() if stderr_pipe is not None else "(unable to read stderr)"
        exc = AgentInvocationError(
            agent_name,
            returncode,
            stderr,
            _bounded_output_lines(
                parsed_output or [],
                explicit_completion_seen=(
                    check_options.explicit_completion_seen if check_options is not None else False
                ),
            ),
        )
        _log_invocation_exit(exc)
        _teardown_subtree_if_pid_available(handle)
        raise exc

    opts = check_options
    if (
        opts is not None
        and opts.execution_strategy is not None
        and opts.execution_strategy.supports_session_continuation()
        and opts.workspace_path is not None
    ):
        bounded_output = _bounded_output_lines(
            parsed_output or [],
            explicit_completion_seen=opts.explicit_completion_seen,
        )
        _eval_fn = (
            opts.evaluate_completion_fn
            if opts.evaluate_completion_fn is not None
            else evaluate_completion
        )
        signals = _eval_fn(
            opts.workspace_path,
            bounded_output,
            required_artifact=opts.required_artifact,
            run_id=_completion_run_id(opts),
        )
        sentinel_check_fn = (
            opts._sentinel_check_fn
            if opts._sentinel_check_fn is not None
            else _check_completion_sentinel
        )
        sentinel_run_id = _completion_run_id(opts)
        sentinel_found = sentinel_check_fn(
            opts.workspace_path,
            sentinel_run_id,
        )
        if sentinel_found:
            signals = replace(
                signals,
                explicit_complete=True,
                completion_sentinel_present=True,
            )
        elif not signals.explicit_complete:
            signals = replace(
                signals,
                explicit_complete=False,
                completion_sentinel_present=False,
            )
        exit_state = opts.execution_strategy.classify_exit(
            handle, signals, liveness_probe=opts.liveness_probe
        )

        if exit_state == AgentExecutionState.RESUMABLE_CONTINUE:
            exit_state = _wait_for_completion_grace(
                handle,
                opts,
                bounded_output,
                clock=_clock,
            )

        if exit_state == AgentExecutionState.WAITING_ON_CHILD:
            exit_state = _wait_for_descendants_then_recheck(
                handle,
                opts,
                bounded_output,
                clock=_clock,
            )

        if exit_state == AgentExecutionState.RESUMABLE_CONTINUE:
            session_id = opts.captured_session_id or extract_transport_session_id(bounded_output)
            raise OpenCodeResumableExitError(agent_name, session_id=session_id)
    elif (
        opts is not None
        and opts.execution_strategy is not None
        and opts.execution_strategy.supports_completion_enforcement()
        and opts.workspace_path is not None
    ):
        bounded_output = _bounded_output_lines(
            parsed_output or [],
            explicit_completion_seen=opts.explicit_completion_seen,
        )
        _eval_fn = (
            opts.evaluate_completion_fn
            if opts.evaluate_completion_fn is not None
            else evaluate_completion
        )
        signals = _eval_fn(
            opts.workspace_path,
            bounded_output,
            required_artifact=opts.required_artifact,
            run_id=_completion_run_id(opts),
        )
        sentinel_check_fn = (
            opts._sentinel_check_fn
            if opts._sentinel_check_fn is not None
            else _check_completion_sentinel
        )
        sentinel_run_id = _completion_run_id(opts)
        sentinel_found = sentinel_check_fn(
            opts.workspace_path,
            sentinel_run_id,
        )
        if sentinel_found:
            signals = replace(
                signals,
                explicit_complete=True,
                completion_sentinel_present=True,
            )
        elif not signals.explicit_complete:
            signals = replace(
                signals,
                explicit_complete=False,
                completion_sentinel_present=False,
            )
        exit_state = opts.execution_strategy.classify_exit(
            handle, signals, liveness_probe=opts.liveness_probe
        )
        if exit_state == AgentExecutionState.RESUMABLE_CONTINUE:
            _teardown_subtree_if_pid_available(handle)
            raise AgentInvocationError(
                agent_name,
                0,
                (
                    "agent exited without required completion evidence "
                    "(no artifact, no declare_complete)"
                ),
            )


def _log_invocation_exit(exc: AgentInvocationError) -> None:
    classified = FailureClassifier().classify(exc, phase="invoke", agent=exc.agent_name)
    retryable = retryable_agent_failure_reason(exc, AgentInactivityTimeoutError) is not None
    if classified.reset_tool_registry or classified.reset_session or retryable:
        logger.warning(
            "Retryable agent exit with code {}: {} [{}]",
            exc.returncode,
            exc.stderr,
            summarize_retry_failure_evidence(exc.parsed_output),
        )
        return
    logger.error(
        "Agent exited with code {}: {} [{}]",
        exc.returncode,
        exc.stderr,
        summarize_retry_failure_evidence(exc.parsed_output),
    )
