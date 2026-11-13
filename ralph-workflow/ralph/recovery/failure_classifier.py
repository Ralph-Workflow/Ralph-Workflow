from __future__ import annotations

import errno
import socket
import urllib.error
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.agents.idle_watchdog_kill import IdleWatchdogKilledError

from .classified_failure import ClassifiedFailure
from .failure_category import FailureCategory
from .failure_details import FailureDetailParts, contains_casefolded_marker, failure_detail_parts
from .unavailability_reason import UnavailabilityReason

if TYPE_CHECKING:
    from .attempt_facts import AttemptFacts

TYPED_SIGNAL_NAMES: frozenset[str] = frozenset(
    {
        "QuotaExhaustedError",
        "MissingCredentialsError",
        "BrokenAgentExitError",
    }
)

_PROVIDER_ORIGINATED_TYPED_SIGNALS: frozenset[str] = frozenset(
    {
        "QuotaExhaustedError",
        "MissingCredentialsError",
    }
)

_PROMPT_ECHO_BROKEN_AGENT_REASON = "prompt_echo"

_TRANSPORT_SUBSTRINGS: frozenset[str] = frozenset(
    {
        "connection reset",
        "socket hang up",
        "ECONNREFUSED",
        "broken pipe",
        "Network is unreachable",
        "Temporary failure in name resolution",
        "Connection refused",
        "Connection timed out",
        "Name or service not known",
        "nodename nor servname provided",
        "connection error",
    }
)

_ENV_ERRNOS: frozenset[int] = frozenset(
    {
        errno.ENETUNREACH,
        errno.ECONNREFUSED,
        errno.EHOSTUNREACH,
        errno.ENETDOWN,
        errno.EPIPE,
        errno.ENOSPC,
    }
)

SESSION_NOT_FOUND_SUBSTRINGS: tuple[str, ...] = (
    "No conversation found with session ID:",
    "Session not found",
    "Unknown session",
    "session does not exist",
)

_TOOL_AVAILABILITY_SUBSTRINGS: tuple[str, ...] = ("no such tool available",)

POST_TOOL_EMPTY_RESPONSE_SUBSTRINGS: tuple[str, ...] = (
    "empty response with no tool calls",
    "empty response",
)

UNAVAILABLE_AGENT_SUBSTRINGS: frozenset[str] = frozenset(
    {
        "agent produced no output",
        "no output for",
        "empty response",
        "timed out with no output",
        "no tool calls",
    }
)

POST_TOOL_ACTIVITY_MARKERS: tuple[str, ...] = (
    '"type":"tool_result"',
    '"type": "tool_result"',
    '"type":"mcp_tool_result"',
    '"type": "mcp_tool_result"',
    '"type":"tool_use"',
    '"type": "tool_use"',
    "[plain] tool:",
    " tool: ",
)

_MISSING_ARTIFACT_SUBSTRINGS: frozenset[str] = frozenset(
    {
        "Missing required artifact",
        "Artifact not found at .agent/artifacts/",
        "required_artifact_missing",
        "Missing/invalid issues artifact",
        "Missing required analysis artifact",
        "Missing fix_result artifact",
        "required artifact receipt missing",
    }
)

_AUTH_CONFIG_SUBSTRINGS: tuple[str, ...] = (
    "authentication required",
    "please run 'agent login'",
    "cursor_api_key",
)

_PERMANENT_ACCOUNT_SUBSTRINGS: tuple[str, ...] = (
    "organization has no valid billing information",
    "your account is not active",
    "payment required",
    "account suspended",
    "subscription expired",
    "trial expired",
)

_MODEL_PREFLIGHT_UNAVAILABLE_SUBSTRINGS: tuple[str, ...] = (
    "opencode local model preflight failed",
)

_SUBSCRIPTION_LIMIT_SUBSTRINGS: tuple[str, ...] = (
    "You've hit your session limit",
    "You've hit your weekly limit",
    "You've hit your Opus limit",
    "Credit balance is too low",
    "You're out of extra usage",
    "Extra usage is required for long context requests",
    "workspace API usage limits",
    "billing_error",
    "you exceeded your current quota",
    "insufficient_quota",
    "insufficient quota",
    "rate limit reached for requests",
    "billing hard limit reached",
    "monthly spend limit reached",
    "selected model is at capacity",
    "you've run out of credits",
    "hit your usage limit",
    "usage limits will reset",
    "set a spend limit to continue",
    "rate_limit_error",
    "number of request tokens has exceeded your per-minute rate limit",
    "number of output tokens has exceeded your per-minute rate limit",
    "out of credits",
    "spend limit reached",
    "rate limit reached",
    "resource_exhausted",
    "resource exhausted",
    "quota exceeded",
    "quota exhausted",
    "individual quota reached",
    "quota limit has been exceeded",
    "rate limit exceeded",
    "too many requests",
    "http 429",
    "status 429",
    "(429)",
    "credits exhausted",
    "no credits remaining",
    "insufficient credits",
    "insufficient balance",
    "usage limit exceeded",
    "request limit exceeded",
    "token limit exceeded",
    "plan limit reached",
    "billing threshold reached",
    "free tier limit exceeded",
    "daily limit reached",
    "hourly limit reached",
    "per-minute limit exceeded",
    "daily limit exceeded",
    "weekly limit exceeded",
    "monthly limit exceeded",
)

_PROVIDER_OVERLOAD_SUBSTRINGS: tuple[str, ...] = (
    "overloaded_error",
    "server cluster is currently under high load",
    "service overloaded",
    "server overloaded",
    "model is overloaded",
    "http 529",
    "status 529",
    "(529)",
    "http 503",
    "status 503",
)

_CONTEXT_EXHAUSTED_SUBSTRINGS: tuple[str, ...] = (
    "context window exceeds limit",
    "context window exceeded",
    "context length exceeded",
    "maximum context length",
    "stopreason=length",
    "stopreason='length'",
    'stopreason":"length',
)

_ARTIFACT_VALIDATION_TYPE_NAMES: frozenset[str] = frozenset(
    {
        "DevelopmentResultValidationError",
        "TypedArtifactValidationError",
        "SmokeTestResultValidationError",
        "ProductSpecValidationError",
        "WorkUnitsValidationError",
    }
)

_WATCHDOG_UNAVAILABILITY_REASONS: frozenset[str] = frozenset(
    {"no_output_at_start", "children_persist_too_long"}
)

if not _WATCHDOG_UNAVAILABILITY_REASONS:
    msg = (
        "_WATCHDOG_UNAVAILABILITY_REASONS must not be empty. An empty"
        " frozenset would silently disable all watchdog-based"
        " unavailability detection; the recovery controller would never"
        " mark an agent as unavailable via the watchdog path. Restore"
        " the canonical 2-element set {no_output_at_start,"
        " children_persist_too_long} in ralph.recovery.failure_classifier."
    )
    raise RuntimeError(msg)
if "no_output_at_start" not in _WATCHDOG_UNAVAILABILITY_REASONS:
    msg = (
        "_WATCHDOG_UNAVAILABILITY_REASONS must contain the string"
        " 'no_output_at_start'. The recovery controller depends on this"
        " watchdog reason always mapping to is_unavailable=True (the"
        " agent is unavailable at session start). Add it to the"
        " constant in ralph.recovery.failure_classifier."
    )
    raise RuntimeError(msg)
if "children_persist_too_long" not in _WATCHDOG_UNAVAILABILITY_REASONS:
    msg = (
        "_WATCHDOG_UNAVAILABILITY_REASONS must contain the string"
        " 'children_persist_too_long'. This watchdog reason is the"
        " cumulative ceiling that bounds live-child stalls and is the"
        " canonical Rule 2 exponential-backoff path when the cumulative"
        " CHILDREN_PERSIST_TOO_LONG ceiling is hit. Add it to the"
        " constant in ralph.recovery.failure_classifier."
    )
    raise RuntimeError(msg)
if "no_progress_quiet" in _WATCHDOG_UNAVAILABILITY_REASONS:
    msg = (
        "_WATCHDOG_UNAVAILABILITY_REASONS must NOT contain the string"
        " 'no_progress_quiet'. The 'no_progress_quiet' reason is handled"
        " ONLY by the conditional 'child_alive in (False, None)' branch"
        " in the is_unavailable predicate, NOT by membership in this"
        " 2-element set. Adding it here would silently route live-child"
        " NO_PROGRESS_QUIET (defense-in-depth Rule 1 path) to Rule 2"
        " exponential backoff and reintroduce the dumb-kill behavior."
        " Remove 'no_progress_quiet' from the constant in"
        " ralph.recovery.failure_classifier."
    )
    raise RuntimeError(msg)
_EXPECTED_WATCHDOG_UNAVAILABILITY_REASONS: frozenset[str] = frozenset(
    {"no_output_at_start", "children_persist_too_long"}
)
if _EXPECTED_WATCHDOG_UNAVAILABILITY_REASONS != _WATCHDOG_UNAVAILABILITY_REASONS:
    msg = (
        "_WATCHDOG_UNAVAILABILITY_REASONS must be exactly the canonical"
        " 2-element set {no_output_at_start, children_persist_too_long}."
        " The current value is "
        f"{set(_WATCHDOG_UNAVAILABILITY_REASONS)!r}. Any widening or"
        " narrowing is a behavior change; update the constant in"
        " ralph.recovery.failure_classifier and the conditional"
        " 'child_alive' branch in the is_unavailable predicate in"
        " lockstep, then update this guard to match."
    )
    raise RuntimeError(msg)


def _is_environmental_exc(exc: BaseException) -> bool:
    if type(exc).__name__ == "McpServerError":
        return True
    if isinstance(exc, ConnectionError | TimeoutError):
        return True
    if isinstance(exc, socket.gaierror):
        return True
    if isinstance(exc, urllib.error.URLError):
        return True
    return isinstance(exc, OSError) and exc.errno in _ENV_ERRNOS


def _message_looks_environmental(msg: str) -> bool:
    lower = msg.lower()
    return any(s.lower() in lower for s in _TRANSPORT_SUBSTRINGS)


def _is_user_config_exc(exc: BaseException) -> bool:
    type_name = type(exc).__name__
    return type_name in {
        "DrainNotBoundError",
        "CheckpointPolicyMismatchError",
        "PolicyValidationError",
        "UnknownAgentError",
        "McpConfigError",
    }


def _is_user_config_message(raw_message: str) -> bool:
    config_substrings = (
        "is not bound",
        "is not defined",
        "Checkpoint was saved at phase",
        "no longer exists in pipeline.toml",
    )
    return any(s in raw_message for s in config_substrings)


def is_missing_artifact_message(raw_message: str) -> bool:
    return any(s in raw_message for s in _MISSING_ARTIFACT_SUBSTRINGS)


def typed_signal_name(exc: BaseException | None) -> str | None:
    if exc is None:
        return None
    name = type(exc).__name__
    if name in TYPED_SIGNAL_NAMES:
        return name
    return None


def typed_signal_provider_originated_for_reason(
    signal_name: str | None,
    reason: str | None,
) -> bool:
    if signal_name is None:
        return False
    if signal_name in _PROVIDER_ORIGINATED_TYPED_SIGNALS:
        return True
    if signal_name == "BrokenAgentExitError":
        return reason == _PROMPT_ECHO_BROKEN_AGENT_REASON
    return False


def typed_signal_provider_originated(exc: BaseException | None) -> bool:
    if exc is None:
        return False
    name = type(exc).__name__
    if name not in TYPED_SIGNAL_NAMES:
        return False
    reason = cast("object", getattr(exc, "reason", None))
    return typed_signal_provider_originated_for_reason(
        name,
        reason if isinstance(reason, str) else None,
    )


def _typed_signal_unavailability_reason(
    signal_name: str | None,
    signal_provider_originated: bool,
) -> UnavailabilityReason | None:
    if signal_name is None or not signal_provider_originated:
        return None
    if signal_name == "QuotaExhaustedError":
        return UnavailabilityReason.OUT_OF_CREDITS
    if signal_name == "MissingCredentialsError":
        return UnavailabilityReason.AUTH_CONFIG
    return None


def is_unsubmitted_artifact_failure(detail_parts: tuple[str, ...] | list[str]) -> bool:
    return contains_casefolded_marker(
        detail_parts, tuple(_MISSING_ARTIFACT_SUBSTRINGS)
    ) or contains_casefolded_marker(detail_parts, POST_TOOL_EMPTY_RESPONSE_SUBSTRINGS)


def classify_failure(exc: BaseException, *, phase: str, agent: str | None) -> ClassifiedFailure:
    return FailureClassifier().classify(exc, phase=phase, agent=agent)


def should_reset_tool_registry(exc: BaseException, *, phase: str, agent: str | None) -> bool:
    return classify_failure(exc, phase=phase, agent=agent).reset_tool_registry


def _is_artifact_validation_message(raw_message: str) -> bool:
    artifact_validation_substrings = (
        "Invalid plan artifact:",
        "Invalid development evidence:",
        "Missing/invalid issues artifact:",
        "Artifact type mismatch:",
        "Artifact content must be a JSON object",
        "must be valid JSON text",
        "must be a JSON object",
        "PROOF INVALID:",
        "PROOF INCOMPLETE:",
        "proof entries are incomplete or invalid",
        "analysis item ID",
        "Unknown plan_item reference",
        "Unknown analysis finding ID",
    )
    return is_missing_artifact_message(raw_message) or any(
        s in raw_message for s in artifact_validation_substrings
    )


def _is_stale_session_message(raw_message: str) -> bool:
    return contains_casefolded_marker((raw_message,), SESSION_NOT_FOUND_SUBSTRINGS)


def _is_tool_dispatch_unregistered_error(exc: BaseException) -> bool:
    if type(exc).__name__ != "ToolDispatchError":
        return False
    message = str(exc)
    return "is not registered" in message.casefold()


def _is_post_tool_empty_response_failure(detail_parts: list[str]) -> bool:
    return contains_casefolded_marker(
        detail_parts, POST_TOOL_EMPTY_RESPONSE_SUBSTRINGS
    ) and contains_casefolded_marker(detail_parts, POST_TOOL_ACTIVITY_MARKERS)


def _is_unavailable_agent_message(msg: str) -> bool:
    lower = msg.casefold()
    return any(marker.casefold() in lower for marker in UNAVAILABLE_AGENT_SUBSTRINGS)


def _is_subscription_limit_message(detail_parts: tuple[str, ...] | list[str]) -> bool:
    provider_parts = (
        detail_parts.provider_parts
        if isinstance(detail_parts, FailureDetailParts)
        else detail_parts
    )
    return contains_casefolded_marker(provider_parts, _SUBSCRIPTION_LIMIT_SUBSTRINGS)


def _provider_unavailability_reason(
    exc: BaseException | None,
    detail_parts: tuple[str, ...] | list[str],
) -> UnavailabilityReason | None:
    type_name = type(exc).__name__ if exc is not None else ""
    if type_name == "PiContextExhaustedExitError" or contains_casefolded_marker(
        detail_parts, _CONTEXT_EXHAUSTED_SUBSTRINGS
    ):
        return UnavailabilityReason.CONTEXT_EXHAUSTED
    if type_name == "PiProviderFailureExitError" or contains_casefolded_marker(
        detail_parts, _PROVIDER_OVERLOAD_SUBSTRINGS
    ):
        return UnavailabilityReason.PROVIDER_UNAVAILABLE
    return None


def _terminal_process_unavailability_reason(
    exc: BaseException | None,
    detail_parts: tuple[str, ...] | list[str],
    connectivity_state: str | None,
) -> UnavailabilityReason | None:
    result: UnavailabilityReason | None = None
    if exc is None:
        return result
    type_name = type(exc).__name__
    supported_type = type_name in {
        "AgentInvocationError",
        "PiContextExhaustedExitError",
        "PiProviderFailureExitError",
    }
    failure_origin = cast("object", getattr(exc, "failure_origin", "agent"))
    agent_origin: bool = supported_type and failure_origin == "agent"
    if contains_casefolded_marker(detail_parts, SESSION_NOT_FOUND_SUBSTRINGS):
        return None
    if agent_origin and contains_casefolded_marker(
        detail_parts, _MODEL_PREFLIGHT_UNAVAILABLE_SUBSTRINGS
    ):
        result = UnavailabilityReason.PROVIDER_UNAVAILABLE
    elif agent_origin and _is_subscription_limit_message(detail_parts):
        result = (
            UnavailabilityReason.OUT_OF_CREDITS
            if (connectivity_state or "").casefold() == "online"
            else None
        )
    elif agent_origin:
        result = _provider_unavailability_reason(exc, detail_parts)
        offline_no_output = (
            connectivity_state or ""
        ).casefold() != "online" and contains_casefolded_marker(detail_parts, _NO_OUTPUT_SUBSTRINGS)
        returncode = cast("object", getattr(exc, "returncode", 0))
        process_failed = (
            type_name == "AgentInvocationError"
            and isinstance(returncode, int)
            and returncode != 0
            and not offline_no_output
        )
        if result is None and process_failed:
            result = UnavailabilityReason.PROCESS_EXITED
    return result


def _apply_terminal_unavailability_category(
    category: FailureCategory,
    counts: bool,
    provider_unavailability: UnavailabilityReason | None,
    reset_tool_registry: bool,
) -> tuple[FailureCategory, bool, UnavailabilityReason | None]:
    if reset_tool_registry and category not in {
        FailureCategory.AGENT,
        FailureCategory.ENVIRONMENTAL,
    }:
        return FailureCategory.AGENT, True, None
    if (
        provider_unavailability is None
        or category not in {FailureCategory.AGENT, FailureCategory.AMBIGUOUS}
        or reset_tool_registry
    ):
        return category, counts, None
    return FailureCategory.AGENT, True, provider_unavailability


def _is_auth_config_failure(detail_parts: tuple[str, ...] | list[str]) -> bool:
    return contains_casefolded_marker(detail_parts, _AUTH_CONFIG_SUBSTRINGS)


def _is_permanent_account_failure(detail_parts: tuple[str, ...] | list[str]) -> bool:
    return contains_casefolded_marker(detail_parts, _PERMANENT_ACCOUNT_SUBSTRINGS)


_NO_OUTPUT_SUBSTRINGS: tuple[str, ...] = (
    "no output",
    "empty output",
    "produced empty output",
    "without producing output",
)

_CONFLICT_ATTEMPT_SUBSTRINGS: tuple[str, ...] = (
    "conflict attempt failed",
    "candidate produced no activity before it exited",
)

_TYPED_EXIT_REASON_CATEGORIES: dict[str, tuple[FailureCategory, bool, bool]] = {
    "PiContextExhaustedExitError": (FailureCategory.AGENT, False, False),
    "PiProviderFailureExitError": (FailureCategory.ENVIRONMENTAL, False, False),
    "QuotaExhaustedError": (FailureCategory.AGENT, False, False),
    "BrokenAgentExitError": (FailureCategory.AGENT, False, False),
    "MissingCredentialsError": (FailureCategory.USER_CONFIG, False, False),
    "AgentNotFound": (FailureCategory.USER_CONFIG, False, False),
}

_TIMEOUT_SUBSTRINGS: tuple[str, ...] = (
    "timed out",
    "timeout",
    "idle",
)


def _is_suspicious_timeout_without_output(
    detail_parts: tuple[str, ...] | list[str],
    connectivity_state: str | None,
) -> bool:
    if (connectivity_state or "").casefold() != "online":
        return False
    has_no_output_signal = contains_casefolded_marker(detail_parts, _NO_OUTPUT_SUBSTRINGS)
    return has_no_output_signal and contains_casefolded_marker(
        detail_parts,
        _TIMEOUT_SUBSTRINGS,
    )


def _classify_unavailability_reason(
    watchdog_reason: str | None,
    detail_parts: tuple[str, ...] | list[str],
    raw_message: str,
    connectivity_state: str | None,
    *,
    child_alive: bool | None = None,
) -> UnavailabilityReason | None:
    reason: UnavailabilityReason | None = None
    if watchdog_reason == "no_output_at_start":
        reason = UnavailabilityReason.NO_OUTPUT_AT_START
    elif watchdog_reason == "no_progress_quiet":
        reason = None if child_alive is True else UnavailabilityReason.STALE_CHILD_QUIET
    elif watchdog_reason == "strictly_stuck":
        reason = UnavailabilityReason.STRICTLY_STUCK
    elif watchdog_reason == "children_persist_too_long":
        reason = UnavailabilityReason.SUSPICIOUS_TIMEOUT_NO_OUTPUT
    elif (connectivity_state or "").casefold() == "online":
        reason = _connectivity_unavailability_reason(detail_parts, connectivity_state)
    if reason is None and contains_casefolded_marker(
        detail_parts, POST_TOOL_EMPTY_RESPONSE_SUBSTRINGS
    ):
        reason = UnavailabilityReason.NO_OUTPUT_AFTER_ACTIVITY
    if reason is None and _is_unavailable_agent_message(raw_message):
        reason = UnavailabilityReason.NO_OUTPUT_AT_START
    return reason


def _select_unavailability_reason(
    *,
    auth_config_failure: bool,
    provider_unavailability: UnavailabilityReason | None,
    typed_unavailability: UnavailabilityReason | None,
    broken_agent: bool,
    is_unavailable: bool,
    watchdog_reason: str | None,
    detail_parts: list[str],
    raw_message: str,
    connectivity_state: str | None,
    child_alive: bool | None,
) -> UnavailabilityReason | None:
    if auth_config_failure:
        return UnavailabilityReason.AUTH_CONFIG
    if provider_unavailability is not None:
        return provider_unavailability
    if typed_unavailability is not None:
        return typed_unavailability
    if broken_agent:
        return UnavailabilityReason.BROKEN_AGENT
    if is_unavailable:
        return _classify_unavailability_reason(
            watchdog_reason,
            detail_parts,
            raw_message,
            connectivity_state,
            child_alive=child_alive,
        )
    return None


def _connectivity_unavailability_reason(
    detail_parts: tuple[str, ...] | list[str],
    connectivity_state: str | None,
) -> UnavailabilityReason | None:
    if _is_subscription_limit_message(detail_parts):
        return UnavailabilityReason.OUT_OF_CREDITS
    if _is_suspicious_timeout_without_output(detail_parts, connectivity_state):
        return UnavailabilityReason.SUSPICIOUS_TIMEOUT_NO_OUTPUT
    return None


class FailureClassifier:
    def classify(
        self,
        exc: BaseException | str,
        *,
        phase: str,
        agent: str | None,
        connectivity_state: str | None = None,
        facts: AttemptFacts | None = None,
    ) -> ClassifiedFailure:
        if isinstance(exc, str):
            raw_message = exc
            original: BaseException | None = None
            detail_parts = [exc]
        else:
            exc_msg = str(exc)
            type_name = type(exc).__name__
            raw_message = f"{type_name}: {exc_msg}" if exc_msg else type_name
            original = exc
            detail_parts = failure_detail_parts(exc)

        exc_obj = exc if isinstance(exc, BaseException) else None
        signal_name = typed_signal_name(exc_obj)
        signal_provider_originated = typed_signal_provider_originated(exc_obj)
        typed_unavailability = _typed_signal_unavailability_reason(
            signal_name,
            signal_provider_originated,
        )
        category, counts, reset_session = self._categorize(
            exc_obj,
            raw_message,
            detail_parts,
            connectivity_state=connectivity_state,
        )
        provider_unavailability = _terminal_process_unavailability_reason(
            exc_obj, detail_parts, connectivity_state
        )

        watchdog_reason = None
        if exc_obj is not None and type(exc_obj).__name__ == "AgentInactivityTimeoutError":
            watchdog_reason_val = cast("object", getattr(exc_obj, "reason", None))
            if watchdog_reason_val is not None:
                watchdog_reason = str(watchdog_reason_val)

        reset_tool_registry = self._is_tool_availability_failure(exc_obj, detail_parts)
        category, counts, provider_unavailability = _apply_terminal_unavailability_category(
            category,
            counts,
            provider_unavailability,
            reset_tool_registry,
        )
        child_alive: bool | None = None
        resumable_session_id: str | None = None
        if exc_obj is not None:
            typed_cause = self._find_typed_watchdog_cause(exc_obj)
            if isinstance(typed_cause, IdleWatchdogKilledError):
                child_alive = typed_cause.child_alive
                cause_session_id: str | None = getattr(typed_cause, "resumable_session_id", None)
                if isinstance(cause_session_id, str) and cause_session_id:
                    resumable_session_id = cause_session_id
        if resumable_session_id is None and exc_obj is not None:
            exc_session_id = cast("object", getattr(exc_obj, "resumable_session_id", None))
            if isinstance(exc_session_id, str) and exc_session_id:
                resumable_session_id = exc_session_id

        typed_quota_exhaustion = (
            exc_obj is not None and type(exc_obj).__name__ == "QuotaExhaustedError"
        )
        broken_agent = exc_obj is not None and type(exc_obj).__name__ == "BrokenAgentExitError"
        base_unavailable = (
            provider_unavailability is not None
            or broken_agent
            or typed_unavailability is not None
            or (
                category == FailureCategory.AGENT
                and (connectivity_state or "").casefold() == "online"
                and not reset_tool_registry
                and not typed_quota_exhaustion
                and (
                    (
                        watchdog_reason in _WATCHDOG_UNAVAILABILITY_REASONS
                        and watchdog_reason != "no_progress_quiet"
                    )
                    or (watchdog_reason == "no_progress_quiet" and child_alive in (False, None))
                    or (
                        (
                            exc_obj is None
                            or type(exc_obj).__name__
                            in {"AgentInvocationError", "AgentInactivityTimeoutError"}
                        )
                        and not (watchdog_reason == "no_progress_quiet" and child_alive is True)
                        and (
                            _is_unavailable_agent_message(raw_message)
                            or contains_casefolded_marker(
                                detail_parts, POST_TOOL_EMPTY_RESPONSE_SUBSTRINGS
                            )
                            or _is_subscription_limit_message(detail_parts)
                        )
                    )
                )
            )
        )
        resumable_kill = (
            base_unavailable
            and watchdog_reason == "no_output_at_start"
            and bool(resumable_session_id)
        )
        auth_config_failure = _is_auth_config_failure(detail_parts)
        is_unavailable = (base_unavailable and not resumable_kill) or auth_config_failure

        unavailability_reason: UnavailabilityReason | None = _select_unavailability_reason(
            auth_config_failure=auth_config_failure,
            provider_unavailability=provider_unavailability,
            typed_unavailability=typed_unavailability,
            broken_agent=broken_agent,
            is_unavailable=is_unavailable,
            watchdog_reason=watchdog_reason,
            detail_parts=detail_parts,
            raw_message=raw_message,
            connectivity_state=connectivity_state,
            child_alive=child_alive,
        )

        if category == FailureCategory.AMBIGUOUS:
            logger.warning(
                "Ambiguous failure classification in phase={} agent={}: "
                "{} [flagged_for_review=true]",
                phase,
                agent,
                raw_message[:200],
            )

        return ClassifiedFailure(
            category=category,
            reason=self._build_reason(category, raw_message, detail_parts),
            attributed_agent=agent if category == FailureCategory.AGENT else None,
            attributed_phase=phase,
            counts_against_budget=counts,
            original_exception=original,
            raw_message=raw_message,
            reset_session=reset_session,
            reset_tool_registry=reset_tool_registry,
            is_unavailable=is_unavailable,
            watchdog_reason=watchdog_reason,
            unavailability_reason=unavailability_reason,
            resumable_session_id=resumable_session_id,
            typed_signal=signal_name,
            typed_signal_provider_originated=signal_provider_originated,
            facts=facts,
        )

    def _is_tool_availability_failure(
        self,
        exc: BaseException | None,
        detail_parts: list[str],
    ) -> bool:
        return (
            (exc is not None and _is_tool_dispatch_unregistered_error(exc))
            or (contains_casefolded_marker(detail_parts, _TOOL_AVAILABILITY_SUBSTRINGS))
            or _is_post_tool_empty_response_failure(detail_parts)
        )

    def _categorize_exc(
        self,
        exc: BaseException,
        raw_message: str,
        detail_parts: list[str],
        *,
        connectivity_state: str | None,
    ) -> tuple[FailureCategory, bool, bool] | None:
        if self._find_typed_watchdog_cause(exc) is not None or contains_casefolded_marker(
            detail_parts, _MODEL_PREFLIGHT_UNAVAILABLE_SUBSTRINGS
        ):
            return FailureCategory.AGENT, True, False
        for predicate, result in (
            (_is_user_config_exc(exc), (FailureCategory.USER_CONFIG, False, False)),
            (
                type(exc).__name__ in _ARTIFACT_VALIDATION_TYPE_NAMES,
                (FailureCategory.ARTIFACT_VALIDATION, False, False),
            ),
            (_is_environmental_exc(exc), (FailureCategory.ENVIRONMENTAL, False, False)),
            (_is_auth_config_failure(detail_parts), (FailureCategory.USER_CONFIG, False, False)),
        ):
            if predicate:
                return result
        type_name = type(exc).__name__
        if type_name in {"AgentInactivityTimeoutError", "BrokenAgentExitError"}:
            return FailureCategory.AGENT, True, type_name == "BrokenAgentExitError"
        if type_name in {
            "QuotaExhaustedError",
            "OpenCodeResumableExitError",
            "AgyIncompleteExitError",
        }:
            return FailureCategory.AGENT, type_name != "QuotaExhaustedError", False
        if type_name == "AgentInvocationError":
            return self._classify_agent_invocation_error(
                exc,
                raw_message,
                detail_parts,
                connectivity_state=connectivity_state,
            )
        return None

    @staticmethod
    def _find_typed_watchdog_cause(
        exc: BaseException,
    ) -> IdleWatchdogKilledError | None:
        if isinstance(exc, IdleWatchdogKilledError):
            return exc
        visited: set[int] = {id(exc)}
        current: BaseException | None = cast(
            "BaseException | None", getattr(exc, "__cause__", None)
        )
        if current is None:
            current = cast("BaseException | None", getattr(exc, "__context__", None))
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            if isinstance(current, IdleWatchdogKilledError):
                return current
            next_cause = cast("BaseException | None", getattr(current, "__cause__", None))
            if next_cause is not None:
                current = next_cause
            else:
                current = cast("BaseException | None", getattr(current, "__context__", None))
        return None

    def _classify_agent_invocation_error(
        self,
        exc: BaseException,
        raw_message: str,
        detail_parts: list[str],
        *,
        connectivity_state: str | None,
    ) -> tuple[FailureCategory, bool, bool] | None:
        failure_origin = cast("object", getattr(exc, "failure_origin", "agent"))
        reset_session = contains_casefolded_marker(detail_parts, SESSION_NOT_FOUND_SUBSTRINGS)
        checks = (
            (failure_origin != "agent", (FailureCategory.AMBIGUOUS, False, False)),
            (reset_session, (FailureCategory.AGENT, True, True)),
            (
                _is_permanent_account_failure(detail_parts),
                (FailureCategory.USER_CONFIG, False, False),
            ),
            (_is_subscription_limit_message(detail_parts), (FailureCategory.AGENT, True, False)),
            (
                _is_suspicious_timeout_without_output(detail_parts, connectivity_state),
                (FailureCategory.AGENT, True, False),
            ),
        )
        for predicate, result in checks:
            if predicate:
                return result
        if not _message_looks_environmental(raw_message) and (
            contains_casefolded_marker(detail_parts, POST_TOOL_EMPTY_RESPONSE_SUBSTRINGS)
            or contains_casefolded_marker(detail_parts, _NO_OUTPUT_SUBSTRINGS)
            or contains_casefolded_marker(detail_parts, _TIMEOUT_SUBSTRINGS)
        ):
            return FailureCategory.AGENT, True, False
        return None

    def _classify_message_only_failure(
        self,
        raw_message: str,
        detail_parts: list[str],
        *,
        connectivity_state: str | None,
    ) -> tuple[FailureCategory, bool, bool] | None:
        checks = (
            (
                raw_message.startswith("integration conflict requires resolution:"),
                (FailureCategory.INTEGRATION, False, False),
            ),
            (
                _is_auth_config_failure(detail_parts),
                (FailureCategory.USER_CONFIG, False, False),
            ),
            (
                contains_casefolded_marker(detail_parts, SESSION_NOT_FOUND_SUBSTRINGS),
                (FailureCategory.AGENT, True, True),
            ),
            (
                _is_permanent_account_failure(detail_parts),
                (FailureCategory.USER_CONFIG, False, False),
            ),
            (
                _is_subscription_limit_message(detail_parts),
                (FailureCategory.AGENT, True, False),
            ),
            (
                _is_suspicious_timeout_without_output(detail_parts, connectivity_state),
                (FailureCategory.AGENT, True, False),
            ),
            (
                _message_looks_environmental(raw_message),
                (FailureCategory.ENVIRONMENTAL, False, False),
            ),
            (
                _is_user_config_message(raw_message),
                (FailureCategory.USER_CONFIG, False, False),
            ),
            (
                _is_artifact_validation_message(raw_message),
                (FailureCategory.ARTIFACT_VALIDATION, False, False),
            ),
            (
                contains_casefolded_marker(detail_parts, _CONFLICT_ATTEMPT_SUBSTRINGS),
                (FailureCategory.AGENT, False, False),
            ),
            (
                raw_message.strip() in _TYPED_EXIT_REASON_CATEGORIES,
                _TYPED_EXIT_REASON_CATEGORIES.get(
                    raw_message.strip(), (FailureCategory.AGENT, False, False)
                ),
            ),
            (
                not _message_looks_environmental(raw_message)
                and (
                    contains_casefolded_marker(detail_parts, POST_TOOL_EMPTY_RESPONSE_SUBSTRINGS)
                    or (
                        (connectivity_state or "").casefold() == "online"
                        and (
                            contains_casefolded_marker(detail_parts, _NO_OUTPUT_SUBSTRINGS)
                            or contains_casefolded_marker(detail_parts, _TIMEOUT_SUBSTRINGS)
                        )
                    )
                ),
                (FailureCategory.AGENT, True, False),
            ),
        )
        for predicate, result in checks:
            if predicate:
                return result
        return None

    def _categorize(
        self,
        exc: BaseException | None,
        raw_message: str,
        detail_parts: list[str],
        *,
        connectivity_state: str | None,
    ) -> tuple[FailureCategory, bool, bool]:
        if exc is not None:
            result = self._categorize_exc(
                exc,
                raw_message,
                detail_parts,
                connectivity_state=connectivity_state,
            )
            if result is not None:
                return result
        result = self._classify_message_only_failure(
            raw_message,
            detail_parts,
            connectivity_state=connectivity_state,
        )
        if result is not None:
            return result
        return FailureCategory.AMBIGUOUS, False, False

    def _build_reason(
        self, category: FailureCategory, raw_message: str, detail_parts: list[str]
    ) -> str:
        prefix_map = {
            FailureCategory.INTEGRATION: "Integration continuation required",
            FailureCategory.ENVIRONMENTAL: "Environmental fault",
            FailureCategory.AGENT: "Agent fault",
            FailureCategory.USER_CONFIG: "Configuration fault",
            FailureCategory.ARTIFACT_VALIDATION: "Artifact validation fault",
            FailureCategory.AMBIGUOUS: "Ambiguous fault (flagged for review)",
        }
        prefix = prefix_map.get(category, "Unknown fault")
        msg = raw_message[:300] if raw_message else "(no message)"
        if _is_subscription_limit_message(detail_parts):
            return f"{prefix}: {msg}; quota or rate limit is exhausted"
        return f"{prefix}: {msg}"
