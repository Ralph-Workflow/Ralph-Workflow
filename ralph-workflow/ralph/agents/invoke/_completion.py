from __future__ import annotations

import contextlib
import json
import re
from dataclasses import KW_ONLY, dataclass, field
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from loguru import logger

from ralph.agents.completion_signals import completion_signals_terminal, evaluate_completion
from ralph.agents.execution_state import (
    AgentExecutionState,
    BaseExecutionStrategy,
    is_prompt_echo_line,
)
from ralph.agents.idle_watchdog import (
    PostExitVerdict,
    PostExitWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
)
from ralph.agents.idle_watchdog._circumstantial_evidence import (
    is_structurally_small_bounded_output,
)
from ralph.agents.invoke._agent_inactivity_timeout_error import AgentInactivityTimeoutError
from ralph.agents.invoke._agy_incomplete_exit_error import raise_missing_completion_evidence
from ralph.agents.invoke._broken_agent_exit_error import BrokenAgentExitError
from ralph.agents.invoke._completion_evidence import (
    apply_sentinel_signal,
    completion_run_id_from_extra_env,
)
from ralph.agents.invoke._completion_exit_status import (
    _MAX_STDERR_CAPTURE_BYTES,
    _truncation_marker,
    credentials_failure_needs_broken_exit,
    looks_like_credentials_failure,
    read_bounded_stderr,
    terminal_returncode,
)
from ralph.agents.invoke._direct_mcp_recovery import (
    condense_failure_text,
    summarize_retry_failure_evidence,
)
from ralph.agents.invoke._errors import (
    AgentInvocationError,
    OpenCodeResumableExitError,
)
from ralph.agents.invoke._fire_reason_issuer import (
    fire_reason_from_value,
    termination_issuer_for_fire_reason,
)
from ralph.agents.invoke._implicit_completion import is_implicit_development_exit
from ralph.agents.invoke._pi_context_exhausted_exit_error import PiContextExhaustedExitError
from ralph.agents.invoke._pi_exit_signals import (
    has_pi_context_exhaustion_signal,
    pi_provider_failure_reason,
)
from ralph.agents.invoke._pi_provider_failure_exit_error import PiProviderFailureExitError
from ralph.agents.invoke._quota_detection import raise_if_quota_exhausted
from ralph.agents.invoke._quota_exhausted_error import QuotaExhaustedError
from ralph.agents.invoke._session import (
    _bounded_output_lines,
    extract_transport_session_id,
    extract_transport_session_id_with_visible_tui,
)
from ralph.agents.registry import lookup_empty_output_diagnostic_factory
from ralph.agents.timeout_clock import Clock, SystemClock
from ralph.pipeline import retryable_failure as _retry
from ralph.process._agent_launch_error import AgentLaunchError
from ralph.process.liveness import DefaultLivenessProbe, LivenessProbe
from ralph.process.teardown import ProcessTeardown, teardown_subtree
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.failure_classifier import (
    SESSION_NOT_FOUND_SUBSTRINGS,
    FailureClassifier,
)
from ralph.recovery.failure_details import contains_casefolded_marker
from ralph.timeout_defaults import BROKEN_AGENT_OUTPUT_GRACE_SECONDS

__all__ = (
    "_MAX_STDERR_CAPTURE_BYTES",
    "CompletionCheckOptions",
    "TerminationRecorder",
    "_truncation_marker",
    "attach_attempt_facts",
    "attempt_facts_for_failure",
    "attempt_facts_from_exception",
    "check_process_result",
    "completion_run_id_from_extra_env",
    "observed_returncode",
    "read_bounded_stderr",
    "termination_issuer_for_exception",
    "termination_issuer_for_fire_reason",
    "termination_issuer_for_teardown_issuer",
)


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.agents.invoke._agent_run_ctx import _EvalCompletionFn
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.process.manager import ManagedProcess, ManagedPtyProcess


_OPERATOR_TEARDOWN_ISSUERS: frozenset[str] = frozenset(
    {
        "interactive_completion",
        "invoke:process",
        "invoke:pty",
        "managed_process.terminate",
        "operator_cancellation",
    }
)

_POST_EXIT_FIRE_VERDICTS: frozenset[PostExitVerdict] = frozenset(
    {PostExitVerdict.FIRE_DESCENDANT_HANG, PostExitVerdict.FIRE_PROCESS_EXIT_HANG}
)

_MAX_OBSERVED_FACT_ENTRIES = 256

_COMPLETION_ISSUER_PREFIX = "invoke:completion:"

_SHELL_SIGNAL_EXIT_MIN = 129
_SHELL_SIGNAL_EXIT_MAX = 159


def _return_code_may_be_a_signal(returncode: int | None) -> bool:
    if returncode is None:
        return True
    return returncode < 0 or _SHELL_SIGNAL_EXIT_MIN <= returncode <= _SHELL_SIGNAL_EXIT_MAX


_EXCEPTION_CHAIN_LIMIT = 8


def termination_issuer_for_teardown_issuer(issuer: str | None) -> TerminationIssuer:
    label = (issuer or "").strip()
    if not label:
        return TerminationIssuer.UNKNOWN
    if label == "quota_exhausted":
        return TerminationIssuer.QUOTA_DETECTOR
    with contextlib.suppress(ValueError):
        return TerminationIssuer(label)
    if label in _OPERATOR_TEARDOWN_ISSUERS or label.startswith(_COMPLETION_ISSUER_PREFIX):
        return TerminationIssuer.OPERATOR
    if fire_reason_from_value(label) is not None:
        return termination_issuer_for_fire_reason(label)
    return TerminationIssuer.UNKNOWN


def _exception_chain(exc: BaseException) -> tuple[BaseException, ...]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and len(chain) < _EXCEPTION_CHAIN_LIMIT:
        chain.append(current)
        cause = current.__cause__
        current = cause if isinstance(cause, BaseException) else None
    return tuple(chain)


def _exception_issuer_label(exc: BaseException) -> str | None:
    for candidate in _exception_chain(exc):
        label = cast("object", getattr(candidate, "issuer", None))
        if isinstance(label, str) and label:
            return label
    return None


def _exception_fire_reason(exc: BaseException) -> WatchdogFireReason | str | None:
    for candidate in _exception_chain(exc):
        reason = cast("object", getattr(candidate, "reason", None))
        if isinstance(reason, WatchdogFireReason):
            return reason
        if isinstance(reason, str) and reason:
            return reason
    return None


def _exception_returncode(exc: BaseException) -> int | None:
    returncode = cast("object", getattr(exc, "returncode", None))
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        return None
    return returncode


def _exception_exit_status(exc: BaseException) -> str | None:
    for candidate in _exception_chain(exc):
        status = cast("object", getattr(candidate, "failure_origin", None))
        if isinstance(status, str) and status:
            return status
    return None


def termination_issuer_for_exception(exc: BaseException) -> TerminationIssuer:
    label = _exception_issuer_label(exc)
    if label is not None:
        return termination_issuer_for_teardown_issuer(label)
    reason = _exception_fire_reason(exc)
    if reason is not None:
        return termination_issuer_for_fire_reason(reason)
    if _return_code_may_be_a_signal(_exception_returncode(exc)):
        return TerminationIssuer.UNKNOWN
    return TerminationIssuer.AGENT_EXIT


def observed_returncode(handle: object) -> int | None:
    poll = cast("Callable[[], int | None] | None", getattr(handle, "poll", None))
    if poll is None:
        return None
    try:
        returncode = poll()
    except Exception:
        return None
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        return None
    return returncode


@dataclass
class _TerminationRecorder:
    _values: list[object] = field(default_factory=lambda: [None] * 7)
    _event_kinds: list[str] = field(default_factory=list)
    _tool_calls: list[str] = field(default_factory=list)
    _pending_tool: str | None = None

    def observe_event(self, kind: str, tool_name: str | None = None) -> None:
        # A tool call counts as completed once any later event proves the stream moved on.
        if self._pending_tool is not None:
            if len(self._tool_calls) < _MAX_OBSERVED_FACT_ENTRIES:
                self._tool_calls.append(self._pending_tool)
            self._pending_tool = None
        is_new_kind = not self._event_kinds or self._event_kinds[-1] != kind
        if is_new_kind and len(self._event_kinds) < _MAX_OBSERVED_FACT_ENTRIES:
            self._event_kinds.append(kind)
        if tool_name is not None:
            self._pending_tool = tool_name

    def record_issuer(self, issuer: TerminationIssuer) -> None:
        if self._values[0] is None:
            self._values[0] = issuer

    def record_observed_returncode(self, returncode: int | None) -> None:
        if returncode is not None:
            self._values[1] = returncode

    def record_reported_returncode(self, returncode: int | None) -> None:
        if returncode is not None:
            self._values[2] = returncode

    def record_post_exit_watchdog_fire(self) -> None:
        self._values[0] = TerminationIssuer.POST_EXIT_WATCHDOG
        self._values[3] = True

    def record_liveness(self, observed: bool | None) -> None:
        if observed is not None and self._values[3] is None:
            self._values[3] = observed

    def record_subagent_activity(self, observed: bool | None) -> None:
        if observed is not None and self._values[4] is None:
            self._values[4] = observed

    def record_elapsed_to_terminal(self, milliseconds: int | None) -> None:
        if milliseconds is not None:
            self._values[5] = milliseconds

    def record_exit_status(self, status: str | None) -> None:
        if status is not None:
            self._values[6] = status

    def termination(self) -> TerminationRecord:
        return TerminationRecord.create(
            cast("TerminationIssuer | None", self._values[0]) or TerminationIssuer.UNKNOWN,
            observed_returncode=cast("int | None", self._values[1]),
            reported_returncode=cast("int | None", self._values[2]),
            liveness_observed=cast("bool | None", self._values[3]),
        )

    def facts(self) -> AttemptFacts:
        return AttemptFacts(
            event_kinds=tuple(self._event_kinds),
            tool_calls=tuple(self._tool_calls),
            liveness_observed=cast("bool | None", self._values[3]),
            subagent_activity_observed=cast("bool | None", self._values[4]),
            elapsed_to_terminal_ms=cast("int | None", self._values[5]),
            exit_status=cast("str | None", self._values[6]),
            termination=self.termination(),
        )


@runtime_checkable
class _AttemptFactsCarrier(Protocol):
    attempt_facts: AttemptFacts


def attach_attempt_facts(exc: BaseException, recorder: _TerminationRecorder) -> None:
    recorder.record_reported_returncode(_exception_returncode(exc))
    facts = recorder.facts()
    carrier = cast("_AttemptFactsCarrier", exc)
    carrier.attempt_facts = facts
    if isinstance(exc, AgentInvocationError) and exc.issuer is None and facts.termination:
        exc.issuer = facts.termination.issuer.value


def attempt_facts_from_exception(exc: BaseException) -> AttemptFacts | None:
    facts = cast("AttemptFacts | None", getattr(exc, "attempt_facts", None))
    return facts if isinstance(facts, AttemptFacts) else None


def attempt_facts_for_failure(exc: BaseException) -> AttemptFacts:
    attached = attempt_facts_from_exception(exc)
    if attached is not None:
        return attached
    return AttemptFacts(
        exit_status=_exception_exit_status(exc),
        termination=TerminationRecord.create(
            termination_issuer_for_exception(exc),
            reported_returncode=_exception_returncode(exc),
        ),
    )


TerminationRecorder = _TerminationRecorder


def _completion_run_id(opts: CompletionCheckOptions) -> str | None:
    return opts.completion_run_id or opts.captured_session_id


def _teardown_subtree_if_pid_available(
    handle: object,
    *,
    issuer: str,
    teardown: ProcessTeardown | None = None,
) -> None:
    pid = cast("int | None", getattr(handle, "pid", None))
    if pid is not None:
        if teardown is None:
            teardown_subtree(pid, issuer=issuer)
        else:
            teardown.teardown_subtree(pid, issuer=issuer)


def _raise_if_pi_reported_failed_exit(
    handle: ManagedProcess | ManagedPtyProcess,
    agent_name: str,
    output: list[str],
    teardown: ProcessTeardown | None,
) -> None:
    if has_pi_context_exhaustion_signal(agent_name, output):
        raise PiContextExhaustedExitError(agent_name)
    provider_failure = pi_provider_failure_reason(agent_name, output)
    if provider_failure is None:
        return
    _teardown_subtree_if_pid_available(
        handle,
        issuer=f"invoke:completion:{agent_name}",
        teardown=teardown,
    )
    raise PiProviderFailureExitError(agent_name, provider_failure)


def _raise_if_transport_reported_error(
    handle: ManagedProcess | ManagedPtyProcess,
    agent_name: str,
    output: list[str],
) -> None:
    for line in output:
        try:
            event = cast("object", json.loads(line))
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and cast("dict[str, object]", event).get("type") == "error":
            raise AgentInvocationError(agent_name, int(handle.returncode or 0), "", output)


def _accepts_implicit_development_exit(
    handle: ManagedProcess | ManagedPtyProcess,
    agent_name: str,
    output: list[str],
    opts: CompletionCheckOptions,
    exit_state: AgentExecutionState,
    teardown: ProcessTeardown | None,
    *,
    waited_on_child: bool = False,
) -> bool:
    if (
        waited_on_child
        or exit_state != AgentExecutionState.RESUMABLE_CONTINUE
        or not is_implicit_development_exit(opts.required_artifact)
    ):
        return False
    _raise_if_transport_reported_error(handle, agent_name, output)
    _raise_if_pi_reported_failed_exit(handle, agent_name, output, teardown)
    return True


def _resolved_session_id(opts: CompletionCheckOptions, output: list[str]) -> str | None:
    session_id = opts.captured_session_id or extract_transport_session_id(output)
    if session_id is not None or not output:
        return session_id
    return next(
        (
            candidate
            for line in output
            if (candidate := extract_transport_session_id_with_visible_tui(line)) is not None
        ),
        None,
    )


@dataclass(frozen=True)
class CompletionCheckOptions:
    execution_strategy: BaseExecutionStrategy | None = None
    workspace_path: Path | None = None
    liveness_probe: LivenessProbe | None = None
    policy: TimeoutPolicy = field(default_factory=lambda: TimeoutPolicy(idle_timeout_seconds=None))
    required_artifact: RequiredArtifact | None = None
    requires_completion_evidence: bool = True
    explicit_completion_seen: bool = False
    captured_session_id: str | None = None
    completion_run_id: str | None = None
    evaluate_completion_fn: _EvalCompletionFn | None = None
    _: KW_ONLY
    last_observed_tool_call: str | None = None
    last_evidence_summary: str | None = None
    elapsed_seconds: float | None = None
    has_meaningful_output: bool | None = None
    input_prompt: str | None = None
    transcript_tail: tuple[str, ...] = ()
    _sentinel_check_fn: Callable[[Path, str | None], bool] | None = field(default=None)
    sentinel_secret: str | None = None
    receipt_secret: str | None = None
    activity_only_supervision: bool = False
    agy_cli_log_path: Path | None = None


def _has_verified_completion(output: list[str], opts: CompletionCheckOptions | None) -> bool:
    if opts is None or opts.workspace_path is None:
        return False
    evaluate = opts.evaluate_completion_fn or evaluate_completion
    signals = evaluate(
        opts.workspace_path,
        output,
        required_artifact=opts.required_artifact,
        run_id=_completion_run_id(opts),
        sentinel_secret=opts.sentinel_secret,
        receipt_secret=opts.receipt_secret,
    )
    return completion_signals_terminal(
        apply_sentinel_signal(signals, opts, sentinel_run_id=_completion_run_id(opts))
    )


def _agy_has_terminal_completion_evidence(
    output: list[str], opts: CompletionCheckOptions | None
) -> bool:
    if (
        opts is None
        or opts.execution_strategy is None
        or not opts.execution_strategy.supports_incomplete_exit_reprompt()
    ):
        return False
    return _has_verified_completion(output, opts)


def _raise_quota_unless_agy_completed(
    agent_name: str,
    stderr_text: str,
    parsed_output: list[str] | None,
    opts: CompletionCheckOptions | None,
) -> None:
    try:
        raise_if_quota_exhausted(
            agent_name,
            stderr_text,
            parsed_output,
            include_output=False,
        )
    except QuotaExhaustedError:
        bounded_output = _bounded_output_lines(
            parsed_output or [],
            explicit_completion_seen=bool(opts and opts.explicit_completion_seen),
        )
        if not _agy_has_terminal_completion_evidence(bounded_output, opts):
            raise


def _recorded_issuer_value(recorder: _TerminationRecorder | None) -> str | None:
    return recorder.termination().issuer.value if recorder is not None else None


def _record_post_exit_watchdog_fire(
    recorder: _TerminationRecorder | None, verdict: PostExitVerdict
) -> None:
    if recorder is not None and verdict in _POST_EXIT_FIRE_VERDICTS:
        recorder.record_post_exit_watchdog_fire()


def _wait_for_completion_grace(
    handle: ManagedProcess | ManagedPtyProcess,
    agent_name: str,
    opts: CompletionCheckOptions,
    parsed_output: list[str],
    *,
    clock: Clock | None = None,
    teardown: ProcessTeardown | None = None,
    termination_recorder: _TerminationRecorder | None = None,
) -> AgentExecutionState:
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
    _record_post_exit_watchdog_fire(termination_recorder, verdict)
    _teardown_subtree_if_pid_available(
        handle,
        issuer=f"invoke:completion:{agent_name}",
        teardown=teardown,
    )
    if verdict == PostExitVerdict.SIGNALS_PRESENT:
        return AgentExecutionState.TERMINAL_COMPLETE
    if verdict == PostExitVerdict.CHILDREN_ACTIVE:
        return AgentExecutionState.WAITING_ON_CHILD
    return AgentExecutionState.RESUMABLE_CONTINUE


def _wait_for_descendants_then_recheck(
    handle: ManagedProcess | ManagedPtyProcess,
    agent_name_or_opts: str | CompletionCheckOptions,
    opts_or_parsed_output: CompletionCheckOptions | list[str],
    parsed_output: list[str] | None = None,
    *,
    clock: Clock | None = None,
    teardown: ProcessTeardown | None = None,
    termination_recorder: _TerminationRecorder | None = None,
) -> AgentExecutionState:
    if isinstance(agent_name_or_opts, CompletionCheckOptions):
        agent_name = "unknown"
        opts = agent_name_or_opts
        if not isinstance(opts_or_parsed_output, list):
            raise TypeError("parsed_output must be a list")
        parsed_output = opts_or_parsed_output
    else:
        agent_name = agent_name_or_opts
        if not isinstance(opts_or_parsed_output, CompletionCheckOptions) or parsed_output is None:
            raise TypeError("completion options and parsed_output are required")
        opts = opts_or_parsed_output

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
    _record_post_exit_watchdog_fire(termination_recorder, verdict)
    _teardown_subtree_if_pid_available(
        handle,
        issuer=f"invoke:completion:{agent_name}",
        teardown=teardown,
    )
    if verdict == PostExitVerdict.SIGNALS_PRESENT:
        return AgentExecutionState.TERMINAL_COMPLETE
    if verdict == PostExitVerdict.QUIESCED_NO_SIGNALS:
        return AgentExecutionState.RESUMABLE_CONTINUE
    return AgentExecutionState.RESUMABLE_CONTINUE


def _raise_if_broken_agent_exit(
    handle: ManagedProcess | ManagedPtyProcess,
    agent_name: str,
    bounded_output: list[str],
    opts: CompletionCheckOptions,
    *,
    stderr_text: str = "",
    teardown: ProcessTeardown | None = None,
) -> None:
    returncode = int(handle.returncode or 0)
    credentials_marker_seen = looks_like_credentials_failure(stderr_text) or any(
        looks_like_credentials_failure(line) for line in bounded_output
    )
    if credentials_marker_seen and (
        not bounded_output or is_structurally_small_bounded_output(bounded_output)
    ):
        _teardown_subtree_if_pid_available(
            handle,
            issuer=f"invoke:completion:{agent_name}",
            teardown=teardown,
        )
        raise BrokenAgentExitError(
            agent_name,
            reason="no_output",
            elapsed_seconds=opts.elapsed_seconds,
            grace_seconds=BROKEN_AGENT_OUTPUT_GRACE_SECONDS,
            returncode=returncode,
            stderr=stderr_text,
        )
    if (
        bounded_output
        and opts.has_meaningful_output is False
        and is_structurally_small_bounded_output(bounded_output)
    ):
        _teardown_subtree_if_pid_available(
            handle,
            issuer=f"invoke:completion:{agent_name}",
            teardown=teardown,
        )
        raise BrokenAgentExitError(
            agent_name,
            reason="no_llm_activity",
            elapsed_seconds=opts.elapsed_seconds,
            grace_seconds=BROKEN_AGENT_OUTPUT_GRACE_SECONDS,
            returncode=returncode,
            stderr=stderr_text,
        )
    if opts.elapsed_seconds is not None and not bounded_output:
        _teardown_subtree_if_pid_available(
            handle,
            issuer=f"invoke:completion:{agent_name}",
            teardown=teardown,
        )
        raise BrokenAgentExitError(
            agent_name,
            reason="no_output",
            elapsed_seconds=opts.elapsed_seconds,
            grace_seconds=BROKEN_AGENT_OUTPUT_GRACE_SECONDS,
            returncode=returncode,
            stderr=stderr_text,
        )
    nonblank_output = [line for line in bounded_output if line.strip()]
    if nonblank_output and all(
        is_prompt_echo_line(line, opts.input_prompt) for line in nonblank_output
    ):
        _teardown_subtree_if_pid_available(
            handle,
            issuer=f"invoke:completion:{agent_name}",
            teardown=teardown,
        )
        raise BrokenAgentExitError(
            agent_name,
            reason="prompt_echo",
            returncode=returncode,
            stderr=stderr_text,
        )


def _has_interactive_completion_bypass(
    handle: ManagedProcess | ManagedPtyProcess,
    parsed_output: list[str] | None,
    check_options: CompletionCheckOptions | None,
) -> bool:
    if check_options is None:
        return False
    with contextlib.suppress(Exception):
        handle_terminal_reason = cast(
            "Callable[[], str | None] | None", getattr(handle, "original_terminal_reason", None)
        )
        if (
            handle_terminal_reason is not None
            and handle_terminal_reason() == "interactive_completion"
        ):
            bounded_output = _bounded_output_lines(
                parsed_output or [],
                explicit_completion_seen=bool(check_options.explicit_completion_seen),
            )
            return bool(_agy_has_terminal_completion_evidence(bounded_output, check_options))
    return False


def _handle_non_zero_returncode(
    handle: ManagedProcess | ManagedPtyProcess,
    agent_name: str,
    parsed_output: list[str] | None,
    check_options: CompletionCheckOptions | None,
    returncode: int,
    stderr_text: str,
    stderr_available: bool,
    process_teardown: ProcessTeardown | None,
    *,
    termination_recorder: _TerminationRecorder | None = None,
) -> None:
    issuer_method = cast(
        "Callable[[], str | None] | None", getattr(handle, "termination_issuer", None)
    )
    issuer = issuer_method() if issuer_method is not None else None
    intentional = returncode in {-15, 143} and isinstance(issuer, str)
    if termination_recorder is not None:
        termination_recorder.record_observed_returncode(observed_returncode(handle))
        termination_recorder.record_reported_returncode(returncode)
        termination_recorder.record_issuer(
            termination_issuer_for_teardown_issuer(issuer)
            if _return_code_may_be_a_signal(returncode)
            else TerminationIssuer.AGENT_EXIT
        )
    bounded_output = _bounded_output_lines(
        parsed_output or [],
        explicit_completion_seen=bool(check_options and check_options.explicit_completion_seen),
    )
    if _has_verified_completion(bounded_output, check_options):
        logger.info(
            "Agent {} exited with code {} after verified completion; accepting as success",
            agent_name,
            returncode,
        )
        _teardown_subtree_if_pid_available(
            handle,
            issuer=f"invoke:completion:{agent_name}",
            teardown=process_teardown,
        )
        return
    raise_if_quota_exhausted(
        agent_name,
        stderr_text,
        parsed_output,
        include_output=True,
    )
    if not intentional:
        _raise_if_pi_reported_failed_exit(handle, agent_name, bounded_output, process_teardown)
    if credentials_failure_needs_broken_exit(
        stderr_text,
        check_options.elapsed_seconds if check_options is not None else None,
    ):
        _teardown_subtree_if_pid_available(
            handle,
            issuer=f"invoke:completion:{agent_name}",
            teardown=process_teardown,
        )
        raise BrokenAgentExitError(
            agent_name,
            reason="no_output",
            elapsed_seconds=check_options.elapsed_seconds if check_options is not None else None,
            grace_seconds=BROKEN_AGENT_OUTPUT_GRACE_SECONDS,
            returncode=returncode,
            stderr=stderr_text,
        )
    stderr = stderr_text if stderr_available else ""
    exc = AgentInvocationError(
        agent_name,
        returncode,
        stderr,
        bounded_output,
        failure_origin=(
            "runtime_launch"
            if isinstance(handle, AgentLaunchError)
            else "intentional_termination"
            if intentional
            else "agent"
        ),
        issuer=issuer if intentional else None,
    )
    log_invocation_exit(exc)
    _teardown_subtree_if_pid_available(
        handle,
        issuer=f"invoke:completion:{agent_name}",
        teardown=process_teardown,
    )
    raise exc


def check_process_result(
    handle: ManagedProcess | ManagedPtyProcess,
    agent_name: str,
    parsed_output: list[str] | None = None,
    check_options: CompletionCheckOptions | None = None,
    *,
    _clock: Clock | None = None,
    process_teardown: ProcessTeardown | None = None,
    termination_recorder: _TerminationRecorder | None = None,
) -> None:
    try:
        _check_process_result(
            handle,
            agent_name,
            parsed_output,
            check_options,
            _clock=_clock,
            process_teardown=process_teardown,
            termination_recorder=termination_recorder,
        )
    except BaseException as exc:
        if termination_recorder is not None:
            attach_attempt_facts(exc, termination_recorder)
        raise


def _record_exit_termination(
    recorder: _TerminationRecorder | None,
    handle: object,
    returncode: int | None,
) -> None:
    if recorder is None:
        return
    recorder.record_observed_returncode(observed_returncode(handle))
    recorder.record_reported_returncode(returncode)
    recorder.record_issuer(TerminationIssuer.AGENT_EXIT)
    recorder.record_liveness(False)


def _check_process_result(
    handle: ManagedProcess | ManagedPtyProcess,
    agent_name: str,
    parsed_output: list[str] | None = None,
    check_options: CompletionCheckOptions | None = None,
    *,
    _clock: Clock | None = None,
    process_teardown: ProcessTeardown | None = None,
    termination_recorder: _TerminationRecorder | None = None,
) -> None:
    if _has_interactive_completion_bypass(handle, parsed_output, check_options):
        _record_exit_termination(termination_recorder, handle, None)
        return

    returncode = terminal_returncode(handle, agent_name=agent_name)
    stderr_text = read_bounded_stderr(handle)
    stderr_attr: object = getattr(handle, "stderr", None)
    stderr_available = stderr_attr is not None
    if returncode != 0:
        _handle_non_zero_returncode(
            handle,
            agent_name,
            parsed_output,
            check_options,
            returncode,
            stderr_text,
            stderr_available,
            process_teardown,
            termination_recorder=termination_recorder,
        )
        return

    _record_exit_termination(termination_recorder, handle, returncode)

    _raise_quota_unless_agy_completed(
        agent_name,
        stderr_text,
        parsed_output,
        check_options,
    )

    opts = check_options
    if opts is not None and not opts.requires_completion_evidence:
        bounded_output = _bounded_output_lines(
            parsed_output or [],
            explicit_completion_seen=opts.explicit_completion_seen,
        )
        _raise_if_pi_reported_failed_exit(
            handle,
            agent_name,
            bounded_output,
            process_teardown,
        )
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
        signals = apply_sentinel_signal(
            signals,
            opts,
            sentinel_run_id=_completion_run_id(opts),
        )
        exit_state = opts.execution_strategy.classify_exit(
            handle, signals, liveness_probe=opts.liveness_probe
        )
        descendants_still_active = False

        if _accepts_implicit_development_exit(
            handle,
            agent_name,
            bounded_output,
            opts,
            exit_state,
            process_teardown,
        ):
            return

        if (
            exit_state == AgentExecutionState.RESUMABLE_CONTINUE
            and not opts.activity_only_supervision
        ):
            exit_state = _wait_for_completion_grace(
                handle,
                agent_name,
                opts,
                bounded_output,
                clock=_clock,
                teardown=process_teardown,
                termination_recorder=termination_recorder,
            )

        if (
            exit_state == AgentExecutionState.WAITING_ON_CHILD
            and not opts.activity_only_supervision
        ):
            exit_state = _wait_for_descendants_then_recheck(
                handle,
                agent_name,
                opts,
                bounded_output,
                clock=_clock,
                teardown=process_teardown,
                termination_recorder=termination_recorder,
            )
            descendants_still_active = (
                opts.execution_strategy.classify_exit(
                    handle,
                    signals,
                    liveness_probe=opts.liveness_probe,
                )
                == AgentExecutionState.WAITING_ON_CHILD
            )

        if exit_state == AgentExecutionState.RESUMABLE_CONTINUE:
            if _accepts_implicit_development_exit(
                handle,
                agent_name,
                bounded_output,
                opts,
                exit_state,
                process_teardown,
                waited_on_child=descendants_still_active,
            ):
                return
            _raise_if_broken_agent_exit(
                handle,
                agent_name,
                bounded_output,
                opts,
                stderr_text=stderr_text,
                teardown=process_teardown,
            )
            session_id = _resolved_session_id(opts, bounded_output)
            _raise_if_pi_reported_failed_exit(
                handle,
                agent_name,
                bounded_output,
                process_teardown,
            )
            raise OpenCodeResumableExitError(
                agent_name,
                session_id=session_id,
                last_observed_tool_call=opts.last_observed_tool_call,
                last_evidence_summary=opts.last_evidence_summary,
                elapsed_seconds=opts.elapsed_seconds,
                transcript_tail=opts.transcript_tail,
                returncode=observed_returncode(handle) or 0,
                issuer=_recorded_issuer_value(termination_recorder),
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
        signals = apply_sentinel_signal(
            signals,
            opts,
            sentinel_run_id=_completion_run_id(opts),
        )
        exit_state = opts.execution_strategy.classify_exit(
            handle, signals, liveness_probe=opts.liveness_probe
        )
        if exit_state == AgentExecutionState.RESUMABLE_CONTINUE:
            if _accepts_implicit_development_exit(
                handle,
                agent_name,
                bounded_output,
                opts,
                exit_state,
                process_teardown,
            ):
                return
            _teardown_subtree_if_pid_available(
                handle,
                issuer=f"invoke:completion:{agent_name}",
                teardown=process_teardown,
            )
            diagnostic_factory = lookup_empty_output_diagnostic_factory(agent_name)
            diagnostic = (
                diagnostic_factory(bounded_output, opts.agy_cli_log_path)
                if diagnostic_factory is not None
                else None
            )
            raise_missing_completion_evidence(
                agent_name,
                opts.execution_strategy,
                diagnostic,
            )


_SESSION_ID_TOKEN_REGEX = r"[A-Za-z0-9._:\-]{4,}"


def _extract_rejected_session_id_from_failure(exc: AgentInvocationError) -> str | None:
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


def log_invocation_exit(exc: AgentInvocationError) -> None:
    classified = FailureClassifier().classify(exc, phase="invoke", agent=exc.agent_name)
    retryable = _retry.retryable_agent_failure_reason(exc, AgentInactivityTimeoutError) is not None
    if classified.reset_session:
        stderr_has_session_marker = contains_casefolded_marker(
            [exc.stderr] if exc.stderr else [], SESSION_NOT_FOUND_SUBSTRINGS
        )
        evidence_field = (
            "(suppressed -- stderr already names the failure)"
            if stderr_has_session_marker
            else summarize_retry_failure_evidence(exc.parsed_output)
        )
        stderr_field = condense_failure_text(exc.stderr) if exc.stderr.strip() else "(empty)"
        rejected_session_id = _extract_rejected_session_id_from_failure(exc)
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
            condense_failure_text(exc.stderr),
            summarize_retry_failure_evidence(exc.parsed_output),
        )
        return
    logger.error(
        "Agent exited with code {}: {} [{}]",
        exc.returncode,
        condense_failure_text(exc.stderr),
        summarize_retry_failure_evidence(exc.parsed_output),
    )
