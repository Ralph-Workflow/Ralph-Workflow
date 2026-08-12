"""Process completion checking and post-exit waiting logic."""

from __future__ import annotations

import json
import re
from dataclasses import KW_ONLY, dataclass, field, replace
from typing import IO, TYPE_CHECKING, cast

from loguru import logger

from ralph.agents._agy_upstream_diagnostic import agy_empty_output_reason
from ralph.agents.completion_signals import (
    CompletionSignals,
    _check_completion_sentinel,
    evaluate_completion,
)
from ralph.agents.execution_state import (
    AgentExecutionState,
    BaseExecutionStrategy,
    is_prompt_echo_line,
)
from ralph.agents.idle_watchdog import PostExitVerdict, PostExitWatchdog, TimeoutPolicy
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._broken_agent_exit_error import BrokenAgentExitError
from ralph.agents.invoke._direct_mcp_recovery import summarize_retry_failure_evidence
from ralph.agents.invoke._errors import AgentInvocationError, OpenCodeResumableExitError
from ralph.agents.invoke._pi_context_exhausted_exit_error import PiContextExhaustedExitError
from ralph.agents.invoke._pi_provider_failure_exit_error import PiProviderFailureExitError
from ralph.agents.invoke._session import (
    _bounded_output_lines,
    extract_transport_session_id,
    extract_transport_session_id_with_visible_tui,
)
from ralph.agents.timeout_clock import Clock, SystemClock
from ralph.mcp.protocol.env import MCP_RUN_ID_ENV
from ralph.pipeline.plumbing.smoke_evidence import Evidence, Provenance
from ralph.pipeline.retryable_failure import retryable_agent_failure_reason
from ralph.process.liveness import DefaultLivenessProbe, LivenessProbe
from ralph.process.teardown import teardown_subtree
from ralph.recovery.failure_classifier import (
    SESSION_NOT_FOUND_SUBSTRINGS,
    FailureClassifier,
)
from ralph.recovery.failure_details import contains_casefolded_marker
from ralph.timeout_defaults import BROKEN_AGENT_OUTPUT_GRACE_SECONDS

#: Hard upper bound on the bytes captured from the subprocess stderr pipe on
#: a non-zero exit. A crashing agent that spews megabytes of traceback to
#: stderr otherwise OOMs the parent. 64 KiB is generous for any human-readable
#: error frame and matches typical subprocess ``stderr=capture`` defaults in
#: the Python ecosystem. When the pipe holds more than this, the captured
#: string is truncated and a ``[stderr truncated: <N> more bytes]`` marker
#: is appended so an operator can still see the truncation (AC-05).
_MAX_STDERR_CAPTURE_BYTES: int = 64 * 1024
_PI_CONTEXT_EXHAUSTED_STOP_REASON = "length"
#: ``message.stopReason`` pi sets when the model turn failed outright
#: (unreachable provider, rejected model, transport fault). The turn
#: produced NO content, so nothing else in the stream names the cause.
_PI_PROVIDER_FAILURE_STOP_REASON = "error"
_PI_PROVIDER_FAILURE_FALLBACK_REASON = "provider reported an unspecified failure"


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
    pid = cast(
        "int | None", getattr(handle, "pid", None)
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    if pid is not None:
        teardown_subtree(pid)


def _is_pi_agent(agent_name: str) -> bool:
    normalized = agent_name.casefold()
    return normalized == "pi" or normalized.startswith("pi/")


def _message_has_length_stop_reason(message: object) -> bool:
    if not isinstance(message, dict):
        return False
    stop_reason = message.get("stopReason")
    return isinstance(stop_reason, str) and (
        stop_reason.casefold() == _PI_CONTEXT_EXHAUSTED_STOP_REASON
    )


def _line_has_pi_context_exhaustion(line: str) -> bool:
    try:
        parsed = cast("object", json.loads(line))
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict):
        return False
    obj = cast("dict[str, object]", parsed)
    assistant_event = obj.get("assistantMessageEvent")
    if isinstance(assistant_event, dict):
        event_dict = cast("dict[str, object]", assistant_event)
        event_type = event_dict.get("type")
        stop_reason = event_dict.get("stopReason")
        return (
            event_type == "done"
            and isinstance(stop_reason, str)
            and stop_reason.casefold() == _PI_CONTEXT_EXHAUSTED_STOP_REASON
        )
    if _message_has_length_stop_reason(obj.get("message")):
        return True
    messages = obj.get("messages")
    if isinstance(messages, list):
        return any(_message_has_length_stop_reason(message) for message in messages)
    return False


def _has_pi_context_exhaustion_signal(agent_name: str, output: list[str]) -> bool:
    if not _is_pi_agent(agent_name):
        return False
    return any(_line_has_pi_context_exhaustion(line) for line in output)


def _message_provider_failure_reason(message: object) -> str | None:
    """Return the ``errorMessage`` of a message whose turn failed outright.

    ``stopReason == 'error'`` is pi's report that the model turn did not
    run at all -- an unreachable provider, a rejected model, a transport
    fault. It is distinct from ``'length'`` (context exhaustion), which
    :func:`_message_has_length_stop_reason` already covers.
    """
    if not isinstance(message, dict):
        return None
    message_dict = cast("dict[str, object]", message)
    stop_reason = message_dict.get("stopReason")
    if not isinstance(stop_reason, str):
        return None
    if stop_reason.casefold() != _PI_PROVIDER_FAILURE_STOP_REASON:
        return None
    error_message = message_dict.get("errorMessage")
    if isinstance(error_message, str) and error_message.strip():
        return error_message
    return _PI_PROVIDER_FAILURE_FALLBACK_REASON


def _exhausted_retry_ladder_reason(obj: dict[str, object]) -> str | None:
    """Return the final error of an exhausted ``auto_retry_end`` ladder.

    ``success=false`` means pi gave up after ``maxAttempts`` and will
    exit rc=0 having done no work.
    """
    if obj.get("type") != "auto_retry_end" or obj.get("success") is not False:
        return None
    final_error = obj.get("finalError")
    if isinstance(final_error, str) and final_error.strip():
        return final_error
    return _PI_PROVIDER_FAILURE_FALLBACK_REASON


def _messages_provider_failure_reason(obj: dict[str, object]) -> str | None:
    """Return the first provider failure across a line's message payloads.

    ``message_end`` / ``turn_end`` carry a single ``message``;
    ``agent_end`` carries a ``messages`` array.
    """
    reason = _message_provider_failure_reason(obj.get("message"))
    if reason is not None:
        return reason
    messages = obj.get("messages")
    if not isinstance(messages, list):
        return None
    for message in cast("list[object]", messages):
        reason = _message_provider_failure_reason(message)
        if reason is not None:
            return reason
    return None


def _line_provider_failure_reason(line: str) -> str | None:
    """Extract a provider-failure reason from one raw pi NDJSON line.

    Checks the exhausted retry ladder first (it carries the most
    authoritative ``finalError``), then the failed message payloads.
    """
    try:
        parsed = cast("object", json.loads(line))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    obj = cast("dict[str, object]", parsed)
    return _exhausted_retry_ladder_reason(obj) or _messages_provider_failure_reason(obj)


def _pi_provider_failure_reason(agent_name: str, output: list[str]) -> str | None:
    """Return why pi's provider failed, or ``None`` if it did not.

    Only consulted for pi agents; other transports report their own
    failures through their own channels.
    """
    if not _is_pi_agent(agent_name):
        return None
    for line in output:
        reason = _line_provider_failure_reason(line)
        if reason is not None:
            return reason
    return None


@dataclass(frozen=True)
class _CompletionCheckOptions:
    execution_strategy: BaseExecutionStrategy | None = None
    workspace_path: Path | None = None
    liveness_probe: LivenessProbe | None = None
    policy: TimeoutPolicy = field(default_factory=lambda: TimeoutPolicy(idle_timeout_seconds=None))
    required_artifact: RequiredArtifact | None = None
    #: False only for a session that can leave no completion evidence: it has
    #: no artifact contract AND is not granted ``artifact.submit``, so
    #: ``declare_complete`` is not in its tool surface. Demanding evidence
    #: there would fail every clean exit on the completion-enforcing
    #: transports. See ``InvokeAgentEffect.requires_completion_evidence``.
    requires_completion_evidence: bool = True
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
    input_prompt: str | None = None
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
    agy_cli_log_path: Path | None = None


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
    workspace: Path = cast(
        "Path", opts.workspace_path
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
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
        sentinel_evidence = Evidence(
            holds=True,
            provenance=Provenance.WORKSPACE_EFFECT,
            detail=(
                "completion sentinel present (graded at "
                "WORKSPACE_EFFECT; upgrade to WIRE via smoke_evidence "
                "grading when wire ledger is in scope)"
            ),
        )
        return replace(
            signals,
            explicit_complete=True,
            completion_sentinel_present=sentinel_evidence,
            completion_sentinel_evidence=sentinel_evidence,
        )
    if not signals.explicit_complete:
        absent_evidence = Evidence(
            holds=False,
            provenance=Provenance.ABSENT,
            detail="completion sentinel not present",
        )
        return replace(
            signals,
            explicit_complete=False,
            completion_sentinel_present=absent_evidence,
            completion_sentinel_evidence=absent_evidence,
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


def _raise_if_broken_agent_exit(
    handle: ManagedProcess | ManagedPtyProcess,
    agent_name: str,
    bounded_output: list[str],
    opts: _CompletionCheckOptions,
) -> None:
    if (
        opts.elapsed_seconds is not None
        and opts.elapsed_seconds >= BROKEN_AGENT_OUTPUT_GRACE_SECONDS
        and not bounded_output
    ):
        _teardown_subtree_if_pid_available(handle)
        raise BrokenAgentExitError(
            agent_name,
            reason="no_output",
            elapsed_seconds=opts.elapsed_seconds,
            grace_seconds=BROKEN_AGENT_OUTPUT_GRACE_SECONDS,
        )
    nonblank_output = [line for line in bounded_output if line.strip()]
    if nonblank_output and all(
        is_prompt_echo_line(line, opts.input_prompt) for line in nonblank_output
    ):
        _teardown_subtree_if_pid_available(handle)
        raise BrokenAgentExitError(agent_name, reason="prompt_echo")


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

    A session that opts out via ``requires_completion_evidence=False`` skips both
    checks: it holds neither an artifact contract nor the ``artifact.submit``
    capability behind ``declare_complete``, so a clean exit is terminal.

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
        stderr_pipe = cast(
            "IO[str] | None", getattr(handle, "stderr", None)
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        stderr = (
            _bounded_read(stderr_pipe) if stderr_pipe is not None else "(unable to read stderr)"
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
    if opts is not None and not opts.requires_completion_evidence:
        # The session had no way to leave completion evidence and no caller
        # waiting to read it: a clean exit is the whole signal.
        return
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
            _raise_if_broken_agent_exit(handle, agent_name, bounded_output, opts)
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
            if _has_pi_context_exhaustion_signal(agent_name, bounded_output):
                raise PiContextExhaustedExitError(agent_name)
            # A dead provider is NOT a resumable session: resuming it
            # relaunches the same agent against the same dead provider,
            # which is the infinite-retry loop this guard exists to break.
            provider_failure = _pi_provider_failure_reason(agent_name, bounded_output)
            if provider_failure is not None:
                _teardown_subtree_if_pid_available(handle)
                raise PiProviderFailureExitError(agent_name, provider_failure)
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
            diagnostic = (
                agy_empty_output_reason(bounded_output, cli_log_path=opts.agy_cli_log_path)
                if agent_name == "agy" or agent_name.startswith("agy/")
                else None
            )
            canonical = (
                "agent exited without required completion evidence "
                "(completion sentinel missing, or required artifact receipt missing)"
            )
            message = f"{diagnostic}\n{canonical}" if diagnostic else canonical
            raise AgentInvocationError(agent_name, 0, message)


#: Canonical session-id character class, shared with the parsers in
#: :mod:`ralph.agents.invoke._session`. Mirrors the
#: ``[A-Za-z0-9._:-]+`` shape used by ``_TRANSPORT_TEXT_SESSION_PATTERNS``
#: so the operator WARNING line surfaces session ids containing ``.`` and
#: ``:`` (e.g. ``abc.def:ghi``) instead of silently dropping them. The
#: minimum-length guard (>= 4 chars) prevents coincidental substrings
#: (e.g. the bare word "session") from being picked up as an id.
_SESSION_ID_TOKEN_REGEX = r"[A-Za-z0-9._:\-]{4,}"


def _extract_rejected_session_id_from_failure(exc: AgentInvocationError) -> str | None:
    """Return the rejected session id extracted from a stale-session failure.

    Scans ``exc.stderr`` and ``exc.parsed_output`` for a session id that
    appears immediately after one of the canonical
    ``SESSION_NOT_FOUND_SUBSTRINGS`` markers. Recognized shapes (single
    source of truth -- the marker vocabulary is the same vocabulary the
    classifier uses to set ``reset_session=True``):

    - ``"Session not found: <id>"``
    - ``"Session not found for ID: <id>"`` (label-separated variant
      already exercised in
      ``tests/test_phases_retry_on_stale_session.py``)
    - ``"Unknown session: <id>"``
    - ``"No conversation found with session ID: <id>"``
    - ``"session does not exist: <id>"``

    The id suffix is required to look id-shaped (the canonical session-id
    character class shared with :mod:`ralph.agents.invoke._session` --
    alphanumeric plus ``-`` / ``_`` / ``.`` / ``:``, length >= 4) so a
    coincidental substring (e.g. the word "session" in a free-form error
    message) is NOT picked up, but valid transport session ids
    containing ``.`` or ``:`` ARE surfaced. AC-02.

    Returns the first matching id, or ``None`` when no canonical
    stale-session marker is present. Single source of truth so the
    operator WARNING line is consistent across all stale-session exits.
    """
    # Match "<marker>(<optional label>)<sep><id>" where <marker> is one of
    # the canonical SESSION_NOT_FOUND_SUBSTRINGS (case-insensitive),
    # <optional label> is a bounded label such as " for ID" / " ID" /
    # " for id" / " id" (the codebase emits label-separated shapes like
    # "Session not found for ID: <id>" in addition to the direct
    # colon-separated shape), <sep> is whitespace or a colon, and <id>
    # is an id-shaped token. The id token regex requires at least 4
    # id-shaped characters -- so a coincidental substring like
    # "session not found" alone does not match, but a valid session id
    # such as ``abc.def:ghi`` (containing ``.`` and ``:``) DOES match.
    _marker_pattern = "|".join(re.escape(m) for m in SESSION_NOT_FOUND_SUBSTRINGS)
    _pattern = re.compile(
        rf"(?i)(?:{_marker_pattern})"
        rf"(?:\s+(?:for\s+ID|for\s+id|ID|id))?"
        rf"[\s:]+({_SESSION_ID_TOKEN_REGEX})",
    )
    haystack = [exc.stderr] if exc.stderr else []
    haystack.extend(exc.parsed_output)
    for line in haystack:
        if not line:
            continue
        match = _pattern.search(line)
        if match is not None:
            return match.group(1)
    return None


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
        rejected_session_id = _extract_rejected_session_id_from_failure(exc)
        # Append-only invariant: new fields are added at the tail of the
        # WARNING line, never in the middle of the existing format string.
        # When the helper returns ``None`` (no canonical marker present),
        # omit the field entirely -- do NOT print ``session_id=None``.
        if rejected_session_id is not None:
            logger.warning(
                "Stale session detected for agent={} (phase=invoke): "
                "resetting session id, retrying with a fresh session. "
                "code={} stderr={} evidence=[{}] session_id={}",
                exc.agent_name,
                exc.returncode,
                stderr_field,
                evidence_field,
                rejected_session_id,
            )
            return
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
