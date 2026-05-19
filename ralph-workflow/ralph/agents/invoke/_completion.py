"""Process completion checking and post-exit waiting logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import IO, TYPE_CHECKING, cast

from loguru import logger

from ralph.agents.completion_signals import evaluate_completion
from ralph.agents.execution_state import (
    AgentExecutionState,
    GenericExecutionStrategy,
    OpenCodeExecutionStrategy,
)
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke._errors import AgentInvocationError, OpenCodeResumableExitError
from ralph.agents.invoke._session import _bounded_output_lines, extract_session_id
from ralph.agents.post_exit_watchdog import PostExitVerdict, PostExitWatchdog
from ralph.agents.timeout_clock import Clock, SystemClock
from ralph.process.liveness import DefaultLivenessProbe, LivenessProbe

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.agents.invoke._agent_run_ctx import _EvalCompletionFn
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.process.manager import ManagedProcess, ManagedPtyProcess


@dataclass(frozen=True)
class _CompletionCheckOptions:
    execution_strategy: GenericExecutionStrategy | OpenCodeExecutionStrategy | None = None
    workspace_path: Path | None = None
    liveness_probe: LivenessProbe | None = None
    policy: TimeoutPolicy = field(default_factory=lambda: TimeoutPolicy(idle_timeout_seconds=None))
    required_artifact: RequiredArtifact | None = None
    explicit_completion_seen: bool = False
    captured_session_id: str | None = None
    evaluate_completion_fn: _EvalCompletionFn | None = None


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
        )
        return execution_strategy.classify_exit(handle, signals, liveness_probe=probe)

    post_exit = PostExitWatchdog(opts.policy, effective_clock)
    verdict = post_exit.wait_parent_exit_grace(classify_exit_state)
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
        )
        return execution_strategy.classify_exit(handle, signals, liveness_probe=probe)

    post_exit = PostExitWatchdog(opts.policy, effective_clock)
    verdict = post_exit.wait_descendant_quiesce(classify_exit_state)
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
        logger.error("Agent exited with code {}: {}", returncode, stderr)
        raise AgentInvocationError(
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
            session_id = opts.captured_session_id or extract_session_id(bounded_output)
            raise OpenCodeResumableExitError(agent_name, session_id=session_id)
