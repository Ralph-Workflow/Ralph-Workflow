"""Process completion checking and post-exit waiting logic."""

from __future__ import annotations

from dataclasses import KW_ONLY, dataclass, field, replace
from typing import IO, TYPE_CHECKING, cast

from loguru import logger

from ralph.agents.completion_signals import (
    CompletionSignals,
    _check_completion_sentinel,
    evaluate_completion,
)
from ralph.agents.execution_state import AgentExecutionState, BaseExecutionStrategy
from ralph.agents.idle_watchdog import PostExitVerdict, PostExitWatchdog, TimeoutPolicy
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._direct_mcp_recovery import summarize_retry_failure_evidence
from ralph.agents.invoke._errors import AgentInvocationError, OpenCodeResumableExitError
from ralph.agents.invoke._session import (
    _bounded_output_lines,
    extract_transport_session_id,
    extract_transport_session_id_with_visible_tui,
)
from ralph.agents.timeout_clock import Clock, SystemClock
from ralph.mcp.protocol.env import MCP_RUN_ID_ENV
from ralph.pipeline.retryable_failure import retryable_agent_failure_reason
from ralph.process.liveness import DefaultLivenessProbe, LivenessProbe
from ralph.process.teardown import teardown_subtree
from ralph.recovery.failure_classifier import (
    SESSION_NOT_FOUND_SUBSTRINGS,
    FailureClassifier,
)
from ralph.recovery.failure_details import contains_casefolded_marker

#: Hard upper bound on the bytes captured from the subprocess stderr pipe on
#: a non-zero exit. A crashing agent that spews megabytes of traceback to
#: stderr otherwise OOMs the parent. 64 KiB is generous for any human-readable
#: error frame and matches typical subprocess ``stderr=capture`` defaults in
#: the Python ecosystem. When the pipe holds more than this, the captured
#: string is truncated and a ``[stderr truncated: <N> more bytes]`` marker
#: is appended so an operator can still see the truncation (AC-05).
_MAX_STDERR_CAPTURE_BYTES: int = 64 * 1024


def _truncation_marker(capped_bytes: int) -> str:
    """Return the canonical truncation marker used when the stderr pipe holds
    more bytes than the cap."""
    return f"\n[stderr truncated: more than {capped_bytes} bytes]"


def _bounded_read(pipe: IO[str]) -> str:
    """Read at most ``_MAX_STDERR_CAPTURE_BYTES`` from ``pipe`` and append a
    truncation marker if more was available.

    The pipe's ``read(size)`` MUST be passed a positive int — calling
    ``read()`` or ``read(-1)`` would be unbounded. The probe for "more was
    available" is a single 1-byte peek AFTER the cap is reached: if it
    succeeds, the pipe is non-empty and we append the marker; otherwise the
    cap read was the entire payload.
    """
    chunk = pipe.read(_MAX_STDERR_CAPTURE_BYTES)
    if len(chunk) >= _MAX_STDERR_CAPTURE_BYTES:
        # Probe one more byte: a successful 1-byte read means the pipe
        # held more than the cap; a 0-byte read means the cap was exact.
        probe = pipe.read(1)
        if probe:
            chunk = chunk + _truncation_marker(_MAX_STDERR_CAPTURE_BYTES)
    return chunk


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
    execution_strategy: BaseExecutionStrategy | None = None
    workspace_path: Path | None = None
    liveness_probe: LivenessProbe | None = None
    policy: TimeoutPolicy = field(default_factory=lambda: TimeoutPolicy(idle_timeout_seconds=None))
    required_artifact: RequiredArtifact | None = None
    explicit_completion_seen: bool = False
    captured_session_id: str | None = None
    completion_run_id: str | None = None
    evaluate_completion_fn: _EvalCompletionFn | None = None
    # R7 (Trustworthy Idle Watchdog spec) root-cause diagnostic
    # fields. Threaded from the line-reader layer at construction
    # time (see ``_process_reader.py:945`` and ``_pty_runner.py:154``)
    # and forwarded to ``OpenCodeResumableExitError`` at the raise
    # site at line 368 below so the diagnostic payload surfaces the
    # captured watchdog state at the moment of the rc=0 exit. The
    # ``KW_ONLY`` sentinel below makes these four fields
    # keyword-only at the dataclass level (Python 3.10+ ``@dataclass``
    # feature) so positional construction of the diagnostic
    # surface is a ``TypeError`` -- callers MUST pass these by
    # keyword. Defaults ``None`` / ``()`` preserve backward
    # compatibility for the original nine fields; only the
    # watchdog-firing path (where the line-reader layer populates
    # ``opts``) carries the diagnostic context. See
    # ``ralph/agents/invoke/_open_code_resumable_exit_error.py`` for
    # the R7 root-cause triage contract.
    _: KW_ONLY
    last_observed_tool_call: str | None = None
    last_evidence_summary: str | None = None
    elapsed_seconds: float | None = None
    transcript_tail: tuple[str, ...] = ()
    _sentinel_check_fn: Callable[[Path, str | None], bool] | None = field(default=None)
    #: RFC-013 P3: broker-owned secret threaded into the sentinel HMAC
    #: verifier on the live read path. ``None`` means the pre-P3
    #: contract (no HMAC verification). Threads only into the default
    #: ``_check_completion_sentinel`` call; the unit-test
    #: ``_sentinel_check_fn`` injection ignores it because the stub
    #: returns a deterministic boolean rather than verifying the
    #: production HMAC.
    sentinel_secret: str | None = None
    #: RFC-013 P3: broker-owned secret threaded into the receipt HMAC
    #: verifier on the live read path. ``None`` means the pre-P3
    #: contract (no HMAC verification). Threads into every
    #: ``evaluate_completion`` call so a forged receipt is rejected
    #: when the broker configures HMAC enforcement.
    receipt_secret: str | None = None


def _apply_sentinel_signal(
    signals: CompletionSignals,
    opts: _CompletionCheckOptions,
    *,
    sentinel_run_id: str | None,
) -> CompletionSignals:
    """Run the configured sentinel check and merge the result into ``signals``.

    When ``opts._sentinel_check_fn`` is set (unit-test stub) it is
    called without any kwargs because its signature is fixed at
    ``(Path, str | None) -> bool``. When it is not set, the live
    ``_check_completion_sentinel`` is called with the
    ``sentinel_secret`` kwarg so the broker-owned HMAC is verified
    on the read path (RFC-013 P3). Extracted from
    ``_check_process_result`` to keep its branch count under the
    PLR0912 cap. ``opts.workspace_path`` is ``Path`` at this point
    (the caller checks ``opts.workspace_path is not None`` before
    entering the session-continuation / completion-enforcement
    branches that reach this helper).
    """
    workspace: Path = cast("Path", opts.workspace_path)
    if opts._sentinel_check_fn is not None:
        sentinel_found = opts._sentinel_check_fn(
            workspace,
            sentinel_run_id,
        )
    else:
        sentinel_found = _check_completion_sentinel(
            workspace,
            sentinel_run_id,
            sentinel_secret=opts.sentinel_secret,
        )
    if sentinel_found:
        return replace(
            signals,
            explicit_complete=True,
            completion_sentinel_present=True,
        )
    if not signals.explicit_complete:
        return replace(
            signals,
            explicit_complete=False,
            completion_sentinel_present=False,
        )
    return signals


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
            sentinel_secret=opts.sentinel_secret,
            receipt_secret=opts.receipt_secret,
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
            sentinel_secret=opts.sentinel_secret,
            receipt_secret=opts.receipt_secret,
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
        stderr = (
            _bounded_read(stderr_pipe)
            if stderr_pipe is not None
            else "(unable to read stderr)"
        )
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
            sentinel_secret=opts.sentinel_secret,
            receipt_secret=opts.receipt_secret,
        )
        signals = _apply_sentinel_signal(
            signals,
            opts,
            sentinel_run_id=_completion_run_id(opts),
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
            if session_id is None and bounded_output:
                # PTY fallback: the bounded_output window may have closed
                # BEFORE the live captured_session_id was read on the live
                # stream, and the legacy extractor returns None for lines
                # that contain ANSI escape codes (the visible-TUI pattern).
                # Iterate the bounded lines and consult the per-line
                # PTY-aware extractor so a session id carried in a TUI
                # banner / status line (e.g. ``\x1b[32mClaude session
                # ready. Session ID: abc123\x1b[0m``) is recovered. The
                # legacy extractor handles plain text + JSON envelopes;
                # the per-line PTY extractor handles ANSI-wrapped text.
                # Use the first non-None result and stop searching. Do
                # NOT widen the OpenCodeResumableExitError signature; the
                # ``session_id`` parameter accepts ``str-or-None``.
                for line in bounded_output:
                    candidate = extract_transport_session_id_with_visible_tui(line)
                    if candidate is not None:
                        session_id = candidate
                        break
            raise OpenCodeResumableExitError(
                agent_name,
                session_id=session_id,
                last_observed_tool_call=opts.last_observed_tool_call,
                last_evidence_summary=opts.last_evidence_summary,
                elapsed_seconds=opts.elapsed_seconds,
                transcript_tail=opts.transcript_tail,
            )
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
            sentinel_secret=opts.sentinel_secret,
            receipt_secret=opts.receipt_secret,
        )
        signals = _apply_sentinel_signal(
            signals,
            opts,
            sentinel_run_id=_completion_run_id(opts),
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
    if classified.reset_session:
        # Stale-session recovery: the operator-visible log line must name the
        # recovery action ("resetting session id, retrying with a fresh
        # session") so this is clearly distinguishable from a generic retryable
        # exit. The "(no output captured)" placeholder from
        # ``summarize_retry_failure_evidence`` is suppressed ONLY when stderr
        # actually carries a stale-session marker -- matching the same
        # ``SESSION_NOT_FOUND_SUBSTRINGS`` vocabulary the classifier already
        # used to set ``reset_session=True``. Generic non-empty stderr (e.g.
        # ``"agent exited"``) is not sufficient: the parsed_output often holds
        # the only concrete stale-session clue (a marker like ``Error: Session
        # not found`` carried in stdout) and the operator must still see it.
        # When stderr is empty, fall back to the summarized evidence (which
        # itself may return "(no output captured)") so the operator still gets
        # a useful diagnostic line. Any future hardening of the evidence
        # payload (e.g. deque(maxlen=N) per AGENTS.md bounded-accumulator rule)
        # is a follow-up; the same risk applies to the existing
        # summarize_retry_failure_evidence path used by the legacy branches.
        stderr_has_session_marker = contains_casefolded_marker(
            [exc.stderr] if exc.stderr else [], SESSION_NOT_FOUND_SUBSTRINGS
        )
        evidence_field = (
            "(suppressed -- stderr already names the failure)"
            if stderr_has_session_marker
            else summarize_retry_failure_evidence(exc.parsed_output)
        )
        stderr_field = exc.stderr if (exc.stderr and exc.stderr.strip()) else "(empty)"
        logger.warning(
            "Stale session detected for agent={} (phase=invoke): "
            "resetting session id, retrying with a fresh session. "
            "code={} stderr={} evidence=[{}]",
            exc.agent_name,
            exc.returncode,
            stderr_field,
            evidence_field,
        )
        return
    if classified.reset_tool_registry or retryable:
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
