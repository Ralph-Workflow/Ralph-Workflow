"""Failure classification logic and rules."""

from __future__ import annotations

import errno
import socket
import urllib.error
from typing import cast

from loguru import logger

from ralph.agents.idle_watchdog_kill import IdleWatchdogKilledError

from .classified_failure import ClassifiedFailure
from .failure_category import FailureCategory
from .failure_details import contains_casefolded_marker, failure_detail_parts
from .unavailability_reason import UnavailabilityReason

# drift-audit: FailureClassifier( is the single-source classifier. The
# 8-file allowlist (enforced by
# tests/test_no_anti_drift_recovery_invariants.py:TestFailureClassifierSingleOwner)
# is the LEGAL constructor set. The 5 actual construction sites are:
#   - ralph/recovery/failure_classifier.py:should_reset_tool_registry (this file)
#   - ralph/recovery/controller.py:RecoveryController.__init__
#   - ralph/agents/invoke/_completion.py:_log_invocation_exit
#   - ralph/pipeline/effect_executor.py:_run_attempt (recovery-decision seam)
#   - ralph/pipeline/agent_retry_decision.py:resolve_retry_intent (recovery-decision seam)
# Consumer-facing callers outside this package MUST route through
# should_reset_tool_registry(...) (this file) — NEVER construct
# FailureClassifier directly. PA-003 extension: pin count is INVARIANT
# (5 actual sites inside 8-file allowlist); do not raise either count.

# Network/transport error substrings that indicate environmental faults
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
    }
)

# OSError error numbers that indicate environmental faults
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

# Substrings that indicate a stale/invalid agent session ID was used for resume.
SESSION_NOT_FOUND_SUBSTRINGS: tuple[str, ...] = (
    "No conversation found with session ID:",
    "Session not found",
    "Unknown session",
    "session does not exist",
)

# Substrings that indicate a backing MCP tool is missing at runtime. The
# live Claude Code failure mode emits
#   `<tool_use_error>Error: No such tool available: mcp__<server>__<tool></tool_use_error>`
# when the agent's `tools/list` snapshot lost the alias (post-restart, post-retry,
# or after a transient MCP recovery) and the call lands in the live server
# with no registered handler. This is a tool-availability failure, NOT a
# stale-session failure: the session is still valid; the tool registry needs
# rebuilding. Routing it to `reset_tool_registry=True` lets the next attempt
# call `RestartAwareMcpBridge.reset_tool_registry()` and recover without
# needing a fresh session.
#
# IMPORTANT: The matching is case-insensitive literal-substring via
# `contains_casefolded_marker`. Do NOT add a literal `"Tool ... is not registered"`
# substring here — `...` would be matched as three literal characters
# (the helper does NOT do regex). The runtime `ToolDispatchError` check is
# handled by a separate class-name + substring helper below.
_TOOL_AVAILABILITY_SUBSTRINGS: tuple[str, ...] = ("no such tool available",)

POST_TOOL_EMPTY_RESPONSE_SUBSTRINGS: tuple[str, ...] = (
    "empty response with no tool calls",
    "empty response",
)

# Substrings that indicate an agent is temporarily unavailable (e.g. out of
# credits). These are matched against the raw failure message.
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
    }
)

# Permanent account/billing/configuration failures that will not self-heal
# after a cooldown. These are matched case-insensitively via
# ``contains_casefolded_marker`` and routed to USER_CONFIG so the pipeline
# terminates through the normal failure path instead of waiting forever.
_PERMANENT_ACCOUNT_SUBSTRINGS: tuple[str, ...] = (
    "organization has no valid billing information",
    "your account is not active",
    "payment required",
    "account suspended",
    "subscription expired",
    "trial expired",
)

# Usage/billing/limit messages that indicate the current agent run cannot
# continue without waiting for a quota reset or adding credits. Patterns are
# matched case-insensitively via ``contains_casefolded_marker``. Add provider-
# specific and cross-provider phrases here; keep them lowercase because the
# helper casefolds both the marker and the detail surface.
_SUBSCRIPTION_LIMIT_SUBSTRINGS: tuple[str, ...] = (
    # Claude Code documented limits
    "You've hit your session limit",
    "You've hit your weekly limit",
    "You've hit your Opus limit",
    "Credit balance is too low",
    "You're out of extra usage",
    "Extra usage is required for long context requests",
    "workspace API usage limits",
    "billing_error",
    # OpenAI credit / quota / billing families
    "you exceeded your current quota",
    "insufficient_quota",
    "insufficient quota",
    "rate limit reached for requests",
    "billing hard limit reached",
    "monthly spend limit reached",
    "you've run out of credits",
    # Anthropic / Claude API families
    "rate_limit_error",
    "number of request tokens has exceeded your per-minute rate limit",
    "number of output tokens has exceeded your per-minute rate limit",
    "out of credits",
    "spend limit reached",
    "rate limit reached",
    # Google / Gemini
    "resource_exhausted",
    "resource exhausted",
    "quota exceeded",
    "quota limit has been exceeded",
    # Cohere / general providers
    "rate limit exceeded",
    "too many requests",
    # Cross-provider generic credit/quota/limit signals
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
    "rate_limited",
    "insufficient_quota",
)

# Typed *ValidationError class names that should route to ARTIFACT_VALIDATION.
# Matched by type(exc).__name__ to avoid circular imports between ralph.recovery
# and ralph.mcp/ralph.pipeline.
_ARTIFACT_VALIDATION_TYPE_NAMES: frozenset[str] = frozenset(
    {
        "PlanArtifactValidationError",
        "DevelopmentResultValidationError",
        "TypedArtifactValidationError",
        "SmokeTestResultValidationError",
        "ProductSpecValidationError",
        "WorkUnitsValidationError",
    }
)


def _is_environmental_exc(exc: BaseException) -> bool:
    """Return True if this exception is clearly an environmental/network fault."""
    if isinstance(exc, ConnectionError | TimeoutError):
        return True
    if isinstance(exc, socket.gaierror):
        return True
    if isinstance(exc, urllib.error.URLError):
        return True
    return isinstance(exc, OSError) and exc.errno in _ENV_ERRNOS


def _message_looks_environmental(msg: str) -> bool:
    """Return True if the error message contains a transport-fault substring."""
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
    """Return True if the message indicates a missing required artifact."""
    return any(s in raw_message for s in _MISSING_ARTIFACT_SUBSTRINGS)


def is_unsubmitted_artifact_failure(detail_parts: tuple[str, ...] | list[str]) -> bool:
    """Return True when an agent finished WITHOUT submitting a required artifact.

    This is the single, shared signal every artifact-requiring caller (commit
    generation, pipeline phases) routes to the resubmit recovery. It covers BOTH
    a clean "completed without writing artifact" exit AND an empty/no-tool-call
    response — both mean nothing was submitted, so both must re-prompt the agent
    to submit (feeding back its prior analysis) rather than restart from scratch.
    """
    return contains_casefolded_marker(
        detail_parts, tuple(_MISSING_ARTIFACT_SUBSTRINGS)
    ) or contains_casefolded_marker(detail_parts, POST_TOOL_EMPTY_RESPONSE_SUBSTRINGS)


def should_reset_tool_registry(exc: BaseException, *, phase: str, agent: str | None) -> bool:
    """Return True when an invocation error indicates a tool-registry reset is needed.

    Single-source-of-truth wrapper that owns the inline
    ``FailureClassifier().classify(...).reset_tool_registry`` lookup so
    the ``commit_plumbing`` module does not need to import or
    construct the classifier directly. The anti-drift pin in
    ``tests/test_no_anti_drift_regression.py`` enforces this
    indirection.
    """
    classified = FailureClassifier().classify(exc, phase=phase, agent=agent)
    return classified.reset_tool_registry


def _is_artifact_validation_message(raw_message: str) -> bool:
    """Return True for artifact/proof validation failures with a typed recovery path."""
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
        "how_to_fix item",
        "Unknown plan_item reference",
        "Unknown how_to_fix_item reference",
    )
    return is_missing_artifact_message(raw_message) or any(
        s in raw_message for s in artifact_validation_substrings
    )


def _is_stale_session_message(raw_message: str) -> bool:
    """Return True if the message indicates a stale agent session ID was used."""
    return contains_casefolded_marker((raw_message,), SESSION_NOT_FOUND_SUBSTRINGS)


def _is_tool_dispatch_unregistered_error(exc: BaseException) -> bool:
    """Detect a runtime `ToolDispatchError` raised by the tool bridge.

    The bridge raises ``ToolDispatchError(f"Tool '{name}' is not registered")``
    at ``ralph/mcp/tools/bridge/_tool_bridge.py:64`` when a tools/call hits
    a name that is not in the live registry. This is the runtime-side
    mirror of the live `No such tool available: mcp__<server>__<tool>`
    failure mode and MUST be classified as a tool-availability failure
    (route to ``reset_tool_registry=True``).

    We distinguish it from the programming-time
    ``ToolRegistrationError`` (raised at bridge construction by
    ``_tool_registration_error.py:8``) by class name: ``ToolDispatchError``
    is the runtime path; ``ToolRegistrationError`` is the bridge-
    construction path. Programming-time errors should NOT trigger a
    registry reset — they indicate a code defect in the bridge builder.
    """
    if type(exc).__name__ != "ToolDispatchError":
        return False
    message = str(exc)
    return "is not registered" in message.casefold()


def _is_post_tool_empty_response_failure(detail_parts: list[str]) -> bool:
    return contains_casefolded_marker(
        detail_parts, POST_TOOL_EMPTY_RESPONSE_SUBSTRINGS
    ) and contains_casefolded_marker(detail_parts, POST_TOOL_ACTIVITY_MARKERS)


def _is_unavailable_agent_message(msg: str) -> bool:
    """Return True when the raw message indicates an unavailable agent."""
    lower = msg.casefold()
    return any(marker.casefold() in lower for marker in UNAVAILABLE_AGENT_SUBSTRINGS)


def _is_subscription_limit_message(detail_parts: tuple[str, ...] | list[str]) -> bool:
    """Return True if the message matches Claude Code documented limit/billing families."""
    return contains_casefolded_marker(detail_parts, _SUBSCRIPTION_LIMIT_SUBSTRINGS)


def _is_permanent_account_failure(detail_parts: tuple[str, ...] | list[str]) -> bool:
    """Return True for permanent account/billing failures that will not self-heal."""
    return contains_casefolded_marker(detail_parts, _PERMANENT_ACCOUNT_SUBSTRINGS)


_NO_OUTPUT_SUBSTRINGS: tuple[str, ...] = (
    "no output",
    "empty output",
    "produced empty output",
    "without producing output",
)

_TIMEOUT_SUBSTRINGS: tuple[str, ...] = (
    "timed out",
    "timeout",
    "idle",
)


def _is_suspicious_timeout_without_output(
    detail_parts: tuple[str, ...] | list[str],
    connectivity_state: str | None,
) -> bool:
    """Return True when a timeout/no-output failure occurs while connectivity is known online."""
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
) -> UnavailabilityReason | None:
    """Classify the unavailability reason from failure signals.

    Priority order (first match wins):
    1. watchdog_reason == "no_output_at_start" -> NO_OUTPUT_AT_START
    2. watchdog_reason == "no_progress_quiet" -> STALE_CHILD_QUIET
       (child alive with stale-progress evidence: heartbeat-only,
        stale-label, or OS-descendant-only — a NEGATIVE signal)
    3. watchdog_reason == "children_persist_too_long" -> SUSPICIOUS_TIMEOUT_NO_OUTPUT
       (cumulative waiting ceiling hit; also a NEGATIVE/stuck signal)
    4. connectivity=online and subscription limit -> OUT_OF_CREDITS
    5. connectivity=online and suspicious timeout -> SUSPICIOUS_TIMEOUT_NO_OUTPUT
    6. unavailable_agent_message text -> NO_OUTPUT_AT_START
    7. post-tool empty response -> NO_OUTPUT_AFTER_ACTIVITY
    8. else None

    NOTE: watchdog_reason takes precedence over text-based detection because
    the watchdog provides structural evidence (alive_by, channel freshness)
    whereas text can be ambiguous.
    """
    reason: UnavailabilityReason | None = None
    if watchdog_reason == "no_output_at_start":
        reason = UnavailabilityReason.NO_OUTPUT_AT_START
    elif watchdog_reason == "no_progress_quiet":
        reason = UnavailabilityReason.STALE_CHILD_QUIET
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


def _connectivity_unavailability_reason(
    detail_parts: tuple[str, ...] | list[str],
    connectivity_state: str | None,
) -> UnavailabilityReason | None:
    """Check connectivity-based unavailability reasons.

    Called when connectivity_state == "online" to check subscription limit
    and suspicious timeout conditions.
    """
    if _is_subscription_limit_message(detail_parts):
        return UnavailabilityReason.OUT_OF_CREDITS
    if _is_suspicious_timeout_without_output(detail_parts, connectivity_state):
        return UnavailabilityReason.SUSPICIOUS_TIMEOUT_NO_OUTPUT
    return None


class FailureClassifier:
    """Classify failures into categories for intelligent recovery routing.

    This is a pure, stateless classifier. All classification rules are
    encapsulated here so new failure modes are added once, not at call sites.
    """

    def classify(
        self,
        exc: BaseException | str,
        *,
        phase: str,
        agent: str | None,
        connectivity_state: str | None = None,
    ) -> ClassifiedFailure:
        """Classify a failure and return a ClassifiedFailure."""
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
        category, counts, reset_session = self._categorize(
            exc_obj,
            raw_message,
            detail_parts,
            connectivity_state=connectivity_state,
        )

        watchdog_reason = None
        if exc_obj is not None and type(exc_obj).__name__ == "AgentInactivityTimeoutError":
            watchdog_reason_val = cast("object", getattr(exc_obj, "reason", None))
            if watchdog_reason_val is not None:
                watchdog_reason = str(watchdog_reason_val)

        # Detect tool-availability failures independently of the category
        # table above. Both the live `No such tool available: mcp__<server>__<tool>`
        # string and the runtime `ToolDispatchError("Tool 'X' is not registered")`
        # exception are routed to `reset_tool_registry=True` so the next
        # attempt calls `RestartAwareMcpBridge.reset_tool_registry()`. The
        # class-name check (`ToolDispatchError` vs `ToolRegistrationError`)
        # prevents the programming-time bridge-construction error from being
        # classified here.
        reset_tool_registry = self._is_tool_availability_failure(exc_obj, detail_parts)
        # Tool-availability failures are agent-side (the agent's tool
        # registry lost an alias) even when the message-based classifier
        # returns AMBIGUOUS. Upgrade the category to AGENT so the
        # failure counts against the agent budget and the recovery
        # controller can route it through its bounded retry path.
        if reset_tool_registry and category not in {
            FailureCategory.AGENT,
            FailureCategory.ENVIRONMENTAL,
        }:
            category = FailureCategory.AGENT
            counts = True

        # Unavailable-agent detection only applies when the failure is agent-side
        # AND connectivity is known healthy AND the failure is not a tool-registry
        # wedge (those have their own bounded retry path). The "no output despite
        # healthy connection" signal is what distinguishes a temporarily
        # unavailable agent (e.g. out of credits) from a transport or offline
        # failure. AgentInactivityTimeoutError subclasses AgentInvocationError,
        # so both class names are accepted here. We match by name rather than
        # isinstance to avoid a circular import through ralph.agents.invoke.
        is_unavailable = (
            category == FailureCategory.AGENT
            and (connectivity_state or "").casefold() == "online"
            and not reset_tool_registry
            and (
                watchdog_reason
                in {"no_progress_quiet", "children_persist_too_long", "no_output_at_start"}
                or (
                    (
                        exc_obj is None
                        or type(exc_obj).__name__
                        in {"AgentInvocationError", "AgentInactivityTimeoutError"}
                    )
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

        unavailability_reason: UnavailabilityReason | None = None
        if is_unavailable:
            unavailability_reason = _classify_unavailability_reason(
                watchdog_reason,
                detail_parts,
                raw_message,
                connectivity_state,
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
            reason=self._build_reason(category, raw_message),
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
        )

    def _is_tool_availability_failure(
        self,
        exc: BaseException | None,
        detail_parts: list[str],
    ) -> bool:
        """Return True when the failure is a backing MCP tool-availability error.

        Two paths qualify:

        1. The exception is a runtime ``ToolDispatchError`` with an
           "is not registered" message (raised at
           ``ralph/mcp/tools/bridge/_tool_bridge.py:64``). Programming-time
           ``ToolRegistrationError`` is excluded by class name.
        2. Any failure detail surface contains the case-insensitive
           substring "no such tool available" — the live Claude Code
           ``<tool_use_error>Error: No such tool available: mcp__<server>__<tool></tool_use_error>``
           message format.
        3. The agent reports an empty response after prior tool activity.
           This is the transport-agnostic post-tool desync family: the
           tool boundary succeeded, but the model failed to continue the turn.
        """
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
        # IdleWatchdogKilledError: classify by the watchdog's typed
        # attributes (reason, signal) — never by the str(exc) text. The
        # exception message may legitimately contain misleading tokens
        # like the word "timeout" (it is in fact a SIGTERM), and the
        # substring-match vocabulary below would mislabel it as a
        # connectivity blip. The check is FIRST so the typed-cause
        # branch wins before any text scanning.
        if isinstance(exc, IdleWatchdogKilledError):
            return FailureCategory.AGENT, True, False
        for predicate, result in (
            (_is_user_config_exc(exc), (FailureCategory.USER_CONFIG, False, False)),
            (
                type(exc).__name__ in _ARTIFACT_VALIDATION_TYPE_NAMES,
                (FailureCategory.ARTIFACT_VALIDATION, False, False),
            ),
            (_is_environmental_exc(exc), (FailureCategory.ENVIRONMENTAL, False, False)),
        ):
            if predicate:
                return result
        type_name = type(exc).__name__
        if type_name == "AgentInactivityTimeoutError":
            return FailureCategory.AGENT, True, False
        if type_name == "AgentInvocationError":
            return self._classify_agent_invocation_error(
                raw_message,
                detail_parts,
                connectivity_state=connectivity_state,
            )
        return None

    def _classify_agent_invocation_error(
        self,
        raw_message: str,
        detail_parts: list[str],
        *,
        connectivity_state: str | None,
    ) -> tuple[FailureCategory, bool, bool] | None:
        reset_session = contains_casefolded_marker(detail_parts, SESSION_NOT_FOUND_SUBSTRINGS)
        if reset_session:
            return FailureCategory.AGENT, True, True
        if _is_permanent_account_failure(detail_parts):
            return FailureCategory.USER_CONFIG, False, False
        if _is_subscription_limit_message(detail_parts):
            return FailureCategory.AGENT, True, False
        if _is_suspicious_timeout_without_output(detail_parts, connectivity_state):
            return FailureCategory.AGENT, True, False
        # Scan the full detail surface (message + stderr + parsed_output) with
        # the shared marker vocabulary, the same surface and vocabulary the
        # pipeline retryable reasoner uses. Checking only ``raw_message`` here
        # let an empty-response signal carried in ``parsed_output`` (e.g. the
        # nanocoder/MiniMax-M3 empty turn) be misclassified as AMBIGUOUS, so the
        # same failure was budget-attributed differently depending on which text
        # surface carried the signal.
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

    def _build_reason(self, category: FailureCategory, raw_message: str) -> str:
        prefix_map = {
            FailureCategory.ENVIRONMENTAL: "Environmental fault",
            FailureCategory.AGENT: "Agent fault",
            FailureCategory.USER_CONFIG: "Configuration fault",
            FailureCategory.ARTIFACT_VALIDATION: "Artifact validation fault",
            FailureCategory.AMBIGUOUS: "Ambiguous fault (flagged for review)",
        }
        prefix = prefix_map.get(category, "Unknown fault")
        msg = raw_message[:300] if raw_message else "(no message)"
        return f"{prefix}: {msg}"
