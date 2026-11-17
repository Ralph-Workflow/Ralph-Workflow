from __future__ import annotations

import weakref
from dataclasses import replace
from typing import TYPE_CHECKING, Literal, NoReturn

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke._fire_reason_issuer import termination_issuer_for_fire_reason
from ralph.agents.invoke._inactivity_timeout_opts import InactivityTimeoutOpts
from ralph.agents.invoke.termination_recorder import (
    TerminationRecorder,
    observed_returncode,
    return_code_may_be_a_signal,
    termination_issuer_for_exception,
    termination_issuer_for_teardown_issuer,
)
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.typed_signal import (
    TypedSignal,
    runtime_issued_termination_is_not_agent_fault,
)

if TYPE_CHECKING:
    from ralph.agents.execution_state import BaseExecutionStrategy
    from ralph.agents.invoke._failure_origin import FailureOrigin

__all__ = [
    "AgentInactivityTimeoutError",
    "AgentInvocationError",
    "AgyIncompleteExitError",
    "AttemptEnded",
    "BrokenAgentExitError",
    "InteractivePermissionPromptError",
    "MissingCredentialsError",
    "OpenCodeResumableExitError",
    "PiContextExhaustedExitError",
    "PiProviderFailureExitError",
    "QuotaExhaustedError",
    "SupervisionInfrastructureError",
    "TerminationRecorder",
    "_IdleStreamTimeoutError",
    "attempt_facts_for_failure",
    "observed_returncode",
    "publish_attempt_facts",
    "raise_missing_completion_evidence",
    "termination_issuer_for_exception",
    "termination_issuer_for_teardown_issuer",
]

_TerminationRecorder = TerminationRecorder

_return_code_may_be_a_signal = return_code_may_be_a_signal

_EXPLICIT_ATTEMPT_FACTS: weakref.WeakKeyDictionary[object, AttemptFacts] = (
    weakref.WeakKeyDictionary()
)


def _exception_returncode(exc: object) -> int | None:
    returncode: object = getattr(exc, "returncode", None)
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        return None
    return returncode


def _own_issuer_label(exc: object) -> str | None:
    label: object = getattr(exc, "issuer", None)
    return label if isinstance(label, str) and label else None


def _own_fire_reason(exc: object) -> WatchdogFireReason | str | None:
    reason: object = getattr(exc, "reason", None)
    if isinstance(reason, WatchdogFireReason):
        return reason
    if isinstance(reason, str) and reason:
        return reason
    return None


def _own_exit_status(exc: object) -> str | None:
    status: object = getattr(exc, "failure_origin", None)
    return status if isinstance(status, str) and status else None


def _own_typed_signal(exc: object, facts: AttemptFacts) -> TypedSignal | None:
    raw: object = getattr(exc, "typed_signal", None)
    if not isinstance(raw, TypedSignal):
        return None
    return replace(
        raw,
        agent_initiated=not runtime_issued_termination_is_not_agent_fault(facts),
    )


def _with_typed_signal(exc: object, facts: AttemptFacts) -> AttemptFacts:
    if facts.typed_signal is not None:
        return facts
    signal = _own_typed_signal(exc, facts)
    if signal is None:
        return facts
    return replace(facts, typed_signal=signal)


def _derived_attempt_facts(exc: object) -> AttemptFacts:
    label = _own_issuer_label(exc)
    if label is not None:
        issuer = termination_issuer_for_teardown_issuer(label)
    else:
        reason = _own_fire_reason(exc)
        if reason is not None:
            issuer = termination_issuer_for_fire_reason(reason)
        elif return_code_may_be_a_signal(_exception_returncode(exc)):
            issuer = TerminationIssuer.UNKNOWN
        else:
            issuer = TerminationIssuer.AGENT_EXIT
    return _with_typed_signal(
        exc,
        AttemptFacts(
            exit_status=_own_exit_status(exc),
            termination=TerminationRecord.create(
                issuer,
                reported_returncode=_exception_returncode(exc),
            ),
        ),
    )



_BrokenAgentReason = Literal["no_output", "prompt_echo", "no_llm_activity"]
_VALID_BROKEN_REASONS = frozenset({"no_output", "prompt_echo", "no_llm_activity"})
_MAX_QUOTA_DETAIL_CHARS = 512


class AttemptEnded:
    def __init__(self, *args: object, facts: AttemptFacts | None = None) -> None:
        if facts is not None:
            _EXPLICIT_ATTEMPT_FACTS[self] = facts
        super().__init__(*args)

    @property
    def facts(self) -> AttemptFacts:
        explicit = _EXPLICIT_ATTEMPT_FACTS.get(self)
        if explicit is not None:
            return explicit
        return _derived_attempt_facts(self)

    @facts.setter
    def facts(self, value: AttemptFacts) -> None:
        _EXPLICIT_ATTEMPT_FACTS[self] = value
        self._on_facts_published(value)

    def _on_facts_published(self, facts: AttemptFacts) -> None:
        del facts


class _IdleStreamTimeoutError(AttemptEnded, RuntimeError):
    __slots__ = (
        "__weakref__",
        "diagnostic",
        "reason",
        "timeout_seconds",
    )

    def __init__(
        self,
        timeout_seconds: float,
        reason: WatchdogFireReason,
        *,
        diagnostic: dict[str, str | int | float | bool | list[object]] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.reason = reason
        self.diagnostic = diagnostic
        if reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG:
            duration = f"{timeout_seconds:.0f}s"
            base_msg = f"Agent kept child agents alive without producing output for {duration}"
            if diagnostic:
                cum = diagnostic.get("cumulative", "?")
                scoped = diagnostic.get("scoped_child_active", "?")
                oldest = diagnostic.get("oldest_child_seconds", "?")
                ws_delta = diagnostic.get("workspace_event_delta", "?")
                lo = diagnostic.get("lifecycle_only_activity", "?")
                msg = (
                    f"{base_msg} (cumulative={cum}s, scoped_child_active={scoped},"
                    f" oldest_child_seconds={oldest}s, workspace_event_delta={ws_delta},"
                    f" lifecycle_only_activity={lo})"
                )
            else:
                msg = base_msg
        elif reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED:
            duration = f"{timeout_seconds:.0f}s"
            msg = f"Agent exceeded max session wall-clock of {duration}"
        elif reason == WatchdogFireReason.PROCESS_EXIT_HANG:
            duration = f"{timeout_seconds:.0f}s"
            msg = f"Agent subprocess closed stdout but did not exit within {duration}"
        elif reason == WatchdogFireReason.STALLED_AFTER_TOOL_RESULT:
            duration = f"{timeout_seconds:.0f}s"
            tool_name = diagnostic.get("last_tool_name", "tool") if diagnostic else "tool"
            msg = (
                f"Agent produced no follow-up output for {duration} after receiving a tool result"
                f" (last_tool={tool_name})"
            )
        elif reason == WatchdogFireReason.REPEATED_ERROR_LOOP:
            fingerprint = diagnostic.get("error_fingerprint") if diagnostic else None
            detail = f" (last error: {fingerprint})" if fingerprint else ""
            msg = (
                "Agent repeated the same error without making forward progress"
                f"{detail}; aborting the retry loop"
            )
        elif reason == WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL:
            tool_name = diagnostic.get("tool_name", "tool") if diagnostic else "tool"
            args_preview = diagnostic.get("tool_args_preview") if diagnostic else None
            detail = f" args={args_preview}" if args_preview else ""
            msg = (
                f"Agent repeated the same {tool_name} tool call{detail} without making"
                " forward progress; aborting the identical-tool-call loop"
            )
        elif reason == WatchdogFireReason.NO_PROGRESS_QUIET:
            duration = f"{timeout_seconds:.0f}s"
            msg = (
                "Agent produced no output and only stale-progress children "
                f"for {duration} (no_progress_quiet trip)"
            )
        else:
            msg = f"Agent produced no output for {timeout_seconds:.0f}s"
        super().__init__(msg)


def publish_attempt_facts(exc: BaseException, recorder: _TerminationRecorder) -> AttemptFacts:
    recorder.record_reported_returncode(_exception_returncode(exc))
    facts = _with_typed_signal(exc, recorder.facts())
    if isinstance(exc, AttemptEnded):
        exc.facts = facts
    return facts


def attempt_facts_for_failure(exc: BaseException) -> AttemptFacts:
    if isinstance(exc, AttemptEnded):
        return exc.facts
    return AttemptFacts()

class AgentInvocationError(AttemptEnded, Exception):
    __slots__ = (
        "agent_name",
        "failure_origin",
        "issuer",
        "parsed_output",
        "returncode",
        "stderr",
    )

    def __init__(
        self,
        agent_name: str,
        returncode: int,
        stderr: str = "",
        parsed_output: list[str] | None = None,
        failure_origin: FailureOrigin = "agent",
        issuer: str | None = None,
    ) -> None:
        self.agent_name = agent_name
        self.returncode = returncode
        self.stderr = stderr
        self.parsed_output = list(parsed_output) if parsed_output is not None else []
        self.failure_origin = failure_origin
        self.issuer = issuer
        detail = self._detail_message()
        suffix = f": {detail}" if detail else ""
        super().__init__(f"Agent '{agent_name}' failed with code {returncode}{suffix}")

    def _detail_message(self) -> str:
        stderr = self.stderr.strip()
        if stderr:
            return stderr
        if self.parsed_output:
            return " | ".join(self.parsed_output)
        return ""

    def _on_facts_published(self, facts: AttemptFacts) -> None:
        termination = facts.termination
        if self.issuer is None and termination is not None:
            self.issuer = termination.issuer.value


class BrokenAgentExitError(AgentInvocationError):
    __slots__ = (
        "elapsed_seconds",
        "grace_seconds",
        "reason",
        "skip_same_agent_retries",
    )

    def __init__(
        self,
        agent_name: str,
        *,
        reason: _BrokenAgentReason,
        elapsed_seconds: float | None = None,
        grace_seconds: float | None = None,
        returncode: int = 0,
        stderr: str = "",
    ) -> None:
        if reason not in _VALID_BROKEN_REASONS:
            msg = f"unsupported broken-agent reason: {reason!r}"
            raise ValueError(msg)
        self.skip_same_agent_retries = True
        self.reason = reason
        self.elapsed_seconds = elapsed_seconds
        self.grace_seconds = grace_seconds
        context = {
            "no_output": "no meaningful LLM output",
            "prompt_echo": "only prompt-echo output",
            "no_llm_activity": "no meaningful LLM activity",
        }[reason]
        super().__init__(
            agent_name,
            returncode,
            stderr
            or f"agent appears broken: {context}; check credentials or provider availability",
        )

    typed_signal_provider_originated: bool = False

    @property
    def typed_signal(self) -> TypedSignal:
        return TypedSignal(
            name=type(self).__name__,
            provider_originated=self.reason == "prompt_echo",
        )


class MissingCredentialsError(AgentInvocationError):
    __slots__ = (
        "env_var",
        "skip_same_agent_retries",
    )
    typed_signal_provider_originated: bool = True

    def __init__(self, agent_name: str, detail: str) -> None:
        self.skip_same_agent_retries = True
        self.env_var = detail.split(maxsplit=1)[0]
        super().__init__(agent_name, 1, detail)

    @property
    def typed_signal(self) -> TypedSignal:
        return TypedSignal(
            name=type(self).__name__,
            provider_originated=type(self).typed_signal_provider_originated,
        )


class PiContextExhaustedExitError(AgentInvocationError):
    __slots__ = ("skip_same_agent_retries",)

    def __init__(self, agent_name: str) -> None:
        self.skip_same_agent_retries = True
        super().__init__(
            agent_name,
            0,
            ("pi agent context length exhausted (stopReason=length; advance to the next agent)"),
        )


class QuotaExhaustedError(AgentInvocationError):
    __slots__ = ("skip_same_agent_retries",)
    typed_signal_provider_originated: bool = True

    def __init__(
        self,
        agent_name: str,
        detail: str | None = None,
        *,
        returncode: int = 0,
        issuer: str = TerminationIssuer.QUOTA_DETECTOR.value,
    ) -> None:
        self.skip_same_agent_retries = True
        excerpt = ""
        if detail:
            excerpt = "".join(
                character if character.isprintable() else " "
                for character in detail[:_MAX_QUOTA_DETAIL_CHARS]
            ).strip()
        message = "quota or rate limit is exhausted"
        if excerpt:
            message = f"{excerpt}; {message}"
        super().__init__(agent_name, returncode, message, issuer=issuer)

    @property
    def typed_signal(self) -> TypedSignal:
        return TypedSignal(
            name=type(self).__name__,
            provider_originated=type(self).typed_signal_provider_originated,
        )


class AgentInactivityTimeoutError(AgentInvocationError):
    __slots__ = (
        "diagnostic",
        "reason",
        "resumable_session_id",
        "runtime_event",
        "session_resume_safe",
        "timeout_seconds",
    )

    def __init__(
        self,
        agent_name: str,
        timeout_seconds: float,
        parsed_output: list[str] | None = None,
        opts: InactivityTimeoutOpts | None = None,
    ) -> None:
        _opts = opts or InactivityTimeoutOpts()
        self.timeout_seconds = timeout_seconds
        self.reason = _opts.reason
        self.diagnostic = dict(_opts.diagnostic) if _opts.diagnostic is not None else {}
        self.session_resume_safe = _opts.session_resume_safe
        self.resumable_session_id = _opts.resumable_session_id
        self.runtime_event = _opts.runtime_event
        if _opts.reason == WatchdogFireReason.OPERATOR_CAP_REACHED:
            duration = f"{timeout_seconds:.0f}s"
            stderr_msg = f"OPERATOR_CAP_REACHED after {duration}; active resolution stopped by configured cap"
        elif _opts.reason == WatchdogFireReason.CONFLICT_INACTIVITY:
            duration = f"{timeout_seconds:.0f}s"
            diagnostic = _opts.diagnostic or {}
            kind = diagnostic.get("last_activity_kind", "none")
            last_at = diagnostic.get("last_activity_at", "never")
            unresolved = diagnostic.get("unresolved_paths", ())
            stderr_msg = (
                f"CONFLICT_INACTIVITY after {duration}; last_activity_kind={kind}; "
                f"last_activity_at={last_at}; unresolved_paths={unresolved}"
            )
        elif _opts.reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG:
            duration = f"{timeout_seconds:.0f}s"
            base_msg = f"Agent kept child agents alive without producing output for {duration}"
            if _opts.diagnostic:
                cum = _opts.diagnostic.get("cumulative", "?")
                scoped = _opts.diagnostic.get("scoped_child_active", "?")
                oldest = _opts.diagnostic.get("oldest_child_seconds", "?")
                ws_delta = _opts.diagnostic.get("workspace_event_delta", "?")
                lo = _opts.diagnostic.get("lifecycle_only_activity", "?")
                stderr_msg = (
                    f"{base_msg} (cumulative={cum}s, scoped_child_active={scoped},"
                    f" oldest_child_seconds={oldest}s, workspace_event_delta={ws_delta},"
                    f" lifecycle_only_activity={lo})"
                )
            else:
                stderr_msg = base_msg
        elif _opts.reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED:
            duration = f"{timeout_seconds:.0f}s"
            stderr_msg = f"Agent exceeded max session wall-clock of {duration}"
        elif _opts.reason == WatchdogFireReason.PROCESS_EXIT_HANG:
            duration = f"{timeout_seconds:.0f}s"
            stderr_msg = f"Agent subprocess closed stdout but did not exit within {duration}"
        elif _opts.reason == WatchdogFireReason.STALLED_AFTER_TOOL_RESULT:
            duration = f"{timeout_seconds:.0f}s"
            tool_name = (
                _opts.diagnostic.get("last_tool_name", "tool") if _opts.diagnostic else "tool"
            )
            stderr_msg = (
                f"Agent produced no follow-up output for {duration} after receiving a tool result"
                f" (last_tool={tool_name})"
            )
        elif _opts.reason == WatchdogFireReason.REPEATED_ERROR_LOOP:
            fingerprint = _opts.diagnostic.get("error_fingerprint") if _opts.diagnostic else None
            detail = f" (last error: {fingerprint})" if fingerprint else ""
            stderr_msg = (
                "Agent repeated the same error without making forward progress"
                f"{detail}; aborting the retry loop"
            )
        elif _opts.reason == WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL:
            tool_name = _opts.diagnostic.get("tool_name", "tool") if _opts.diagnostic else "tool"
            args_preview = _opts.diagnostic.get("tool_args_preview") if _opts.diagnostic else None
            detail = f" args={args_preview}" if args_preview else ""
            stderr_msg = (
                f"Agent repeated the same {tool_name} tool call{detail} without making"
                " forward progress; aborting the identical-tool-call loop"
            )
        elif _opts.reason == WatchdogFireReason.NO_PROGRESS_QUIET:
            duration = f"{timeout_seconds:.0f}s"
            stderr_msg = (
                "Agent produced no output and only stale-progress children "
                f"for {duration} (no_progress_quiet trip)"
            )
        elif _opts.reason == WatchdogFireReason.NO_OUTPUT_AT_START:
            duration = f"{timeout_seconds:.0f}s"
            stderr_msg = (
                f"Agent produced no output for {duration} (no_output_at_start trip"
                " — invocation never recorded any activity)"
            )
        else:
            stderr_msg = f"Agent produced no output for {timeout_seconds:.0f}s"
        super().__init__(
            agent_name,
            -1,
            stderr_msg,
            list(parsed_output) if parsed_output is not None else [],
            failure_origin="watchdog_observation",
            issuer=termination_issuer_for_fire_reason(_opts.reason).value,
        )



class AgyIncompleteExitError(AgentInvocationError):
    def __init__(self, agent_name: str, diagnostic: str | None = None) -> None:
        canonical = (
            "agent exited without required completion evidence "
            "(completion sentinel missing, or required artifact receipt missing). "
            "Ralph issues exactly one automatic recovery reprompt per invocation "
            "for this condition; seeing this error means that reprompt was "
            "already spent or did not produce the evidence. Action: inspect the "
            "agent transcript — the agent must finish the assigned work "
            "autonomously (no interactive input is available), submit any "
            "required artifact via the ralph_submit_md_artifact MCP tool, and "
            "call declare_complete before exiting."
        )
        message = f"{diagnostic}\n{canonical}" if diagnostic else canonical
        super().__init__(agent_name, 0, message)


class PiProviderFailureExitError(AgentInvocationError):
    __slots__ = ("skip_same_agent_retries",)

    def __init__(self, agent_name: str, reason: str) -> None:
        self.skip_same_agent_retries = True
        super().__init__(
            agent_name,
            0,
            f"pi agent provider failure (stopReason=error): {reason}",
        )


class SupervisionInfrastructureError(AgentInvocationError):
    __slots__ = ("detail",)

    def __init__(self, agent_name: str, detail: str) -> None:
        self.detail = detail
        super().__init__(
            agent_name,
            -1,
            f"SUPERVISION_INFRASTRUCTURE_FAILURE: {detail}",
            [],
        )


class InteractivePermissionPromptError(AgentInvocationError):
    def __init__(self, agent_name: str, parsed_output: list[str]) -> None:
        super().__init__(
            agent_name,
            -1,
            "Interactive Claude reached a permission prompt in unattended mode",
            parsed_output,
        )


class OpenCodeResumableExitError(AgentInvocationError):
    __slots__ = (
        "elapsed_seconds",
        "last_evidence_summary",
        "last_observed_tool_call",
        "resumable_session_id",
        "transcript_tail",
    )

    def __init__(
        self,
        agent_name: str,
        session_id: str | None = None,
        *,
        last_observed_tool_call: str | None = None,
        last_evidence_summary: str | None = None,
        elapsed_seconds: float | None = None,
        transcript_tail: tuple[str, ...] = (),
        returncode: int = 0,
        issuer: str | None = None,
    ) -> None:
        self.resumable_session_id = session_id
        self.last_observed_tool_call = last_observed_tool_call
        self.last_evidence_summary = last_evidence_summary
        self.elapsed_seconds = elapsed_seconds
        self.transcript_tail = transcript_tail
        base_message = (
            "agent session exited without required completion evidence "
            "(completion sentinel missing, or required artifact receipt missing)"
        )
        diagnostic_suffix_parts: list[str] = []
        if last_observed_tool_call is not None:
            diagnostic_suffix_parts.append(f"last_tool_call={last_observed_tool_call}")
        if elapsed_seconds is not None:
            diagnostic_suffix_parts.append(f"elapsed={round(elapsed_seconds, 1)}s")
        if diagnostic_suffix_parts:
            full_message = base_message + " [" + ", ".join(diagnostic_suffix_parts) + "]"
        else:
            full_message = base_message
        super().__init__(agent_name, returncode, full_message, issuer=issuer)


def raise_missing_completion_evidence(
    agent_name: str,
    execution_strategy: BaseExecutionStrategy,
    diagnostic: str | None,
) -> NoReturn:
    if execution_strategy.supports_incomplete_exit_reprompt():
        raise AgyIncompleteExitError(agent_name, diagnostic=diagnostic)
    canonical = (
        "agent exited without required completion evidence "
        "(completion sentinel missing, or required artifact receipt missing)"
    )
    message = f"{diagnostic}\n{canonical}" if diagnostic else canonical
    raise AgentInvocationError(agent_name, 0, message)
