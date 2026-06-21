"""Failure classification logic and rules.

The classifier walks the full ``__cause__`` / ``__context__`` chain
when looking for the typed ``IdleWatchdogKilledError`` (AC-05). The
typed-attribute branch wins before any text scanning so a SIGTERM
relabeled as a connectivity blip because the agent's stderr contained
the word "timeout" is still classified as AGENT, even when the
typed cause is buried several layers deep in the wrapped exception
chain.
"""

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

# Watchdog fire-reasons that always mark the agent as unavailable
# (Rule 2: exponential backoff to the next agent) — with the
# exception of ``no_progress_quiet``, which is a CONDITIONAL
# branch (depends on the typed ``child_alive`` signal). The
# constant is the 2-element hard-coded set; the conditional
# ``no_progress_quiet`` branch is added by the ``is_unavailable``
# predicate when ``child_alive is False`` (truly dead child) OR
# ``child_alive is None`` (legacy default — conservative policy
# preserves the original 3-element set behavior).
#
# Conservative policy:
#   - ``child_alive is None`` (legacy default — no signal at
#     all) preserves the original STALE_CHILD_QUIET (Rule 2)
#     behavior for backward-compat with the 14 existing tests
#     in test_unavailability_reason.py that do not set
#     child_alive.
#   - ``child_alive is False`` (truly dead child — the
#     corroborator returned alive_by=None) routes to
#     STALE_CHILD_QUIET (Rule 2). This is the only path where
#     NO_PROGRESS_QUIET can fire under the wt-012 gate
#     refinement.
#   - ``child_alive is True`` (live child — defense-in-depth;
#     normally dead code because the gate refinement defers
#     the fire) routes to is_unavailable=False (Rule 1: same-
#     agent retry).
_WATCHDOG_UNAVAILABILITY_REASONS: frozenset[str] = frozenset(
    {"no_output_at_start", "children_persist_too_long"}
)

# Import-time invariant: pin the canonical 2-element set so a
# future PR cannot silently widen the set, drop an element, or
# move ``no_progress_quiet`` back into the constant without
# updating the failure classifier. The ``if/raise RuntimeError``
# (NOT ``assert``) idiom makes the invariant survive ``python -O``
# per AGENTS.md 'Non-negotiables'. The checks are:
#   1. the constant is non-empty (an empty set would silently
#      disable all watchdog-based unavailability detection);
#   2. the constant contains the string ``no_output_at_start``
#      (the operator-visible hard failure mode that the
#      recovery controller must always treat as unavailable);
#   3. the constant contains the string ``children_persist_too_long``
#      (the cumulative ceiling that bounds live-child stalls);
#   4. the constant is exactly the canonical 2-element set
#      {``no_output_at_start``, ``children_persist_too_long``} --
#      nothing more, nothing less. Any widening or narrowing
#      is a behavior change and must update the constant
#      explicitly so this guard trips;
#   5. the constant does NOT contain ``no_progress_quiet``
#      (that reason is handled by the separate ``child_alive``
#      conditional branch in the ``is_unavailable`` predicate,
#      NOT by membership in this set).
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
    *,
    child_alive: bool | None = None,
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

    The ``child_alive`` parameter is consumed ONLY by the
    ``no_progress_quiet`` branch. The conservative policy:

      - ``child_alive is True`` (live child — defense-in-depth;
        normally dead code because the wt-012 gate refinement
        in ``IdleWatchdog._is_no_progress_quiet`` defers the
        fire when alive_by is not None): returns ``None`` here
        so the ``is_unavailable`` predicate below takes the
        Rule 1 path (defense-in-depth; same-agent retry).
      - ``child_alive is False`` (truly dead child — the
        corroborator returned alive_by=None): routes to
        ``STALE_CHILD_QUIET`` (Rule 2: exponential backoff).
        This is the only path where NO_PROGRESS_QUIET can
        fire under the wt-012 gate refinement.
      - ``child_alive is None`` (legacy default — no signal at
        all): routes to ``STALE_CHILD_QUIET`` (Rule 2). This
        preserves the original behavior for the 14 existing
        tests in test_unavailability_reason.py that do not set
        child_alive.

    NOTE: watchdog_reason takes precedence over text-based detection because
    the watchdog provides structural evidence (alive_by, channel freshness)
    whereas text can be ambiguous.
    """
    reason: UnavailabilityReason | None = None
    if watchdog_reason == "no_output_at_start":
        reason = UnavailabilityReason.NO_OUTPUT_AT_START
    elif watchdog_reason == "no_progress_quiet":
        # Per the PROMPT: NOT ALL stuck retries should advance the
        # chain. The conservative policy: child_alive=None (default
        # — no signal at all) preserves the original STALE_CHILD_QUIET
        # (Rule 2) behavior for backward-compat. child_alive=False
        # (truly dead child — no corroborator signal) also routes to
        # STALE_CHILD_QUIET (Rule 2). child_alive=True (live child)
        # routes to None here so the is_unavailable predicate below
        # can take the Rule 1 branch (defense-in-depth; normally dead
        # code because the wt-012 gate refinement defers the fire
        # when alive_by is not None).
        reason = None if child_alive is True else UnavailabilityReason.STALE_CHILD_QUIET
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

        # Walk the ``__cause__`` chain to find the typed
        # ``IdleWatchdogKilledError`` and read the ``child_alive`` field.
        # The watchdog attaches the typed exception to
        # ``_IdleStreamTimeoutError.__cause__`` (see
        # ``_process_reader.py`` and ``_pty_line_reader.py``) so the
        # classifier can read the live-child signal end-to-end. The
        # walk is shallow (``__cause__`` only — the watchdog attaches
        # the typed exception directly to the wrapper) because the
        # chain is already validated by ``_find_typed_watchdog_cause``
        # in the categorization pass above; here we only need the
        # direct cause for the ``child_alive`` field.
        child_alive: bool | None = None
        if exc_obj is not None:
            direct_cause = cast("BaseException | None", getattr(exc_obj, "__cause__", None))
            if isinstance(direct_cause, IdleWatchdogKilledError):
                child_alive = direct_cause.child_alive

        # Unavailable-agent detection only applies when the failure is agent-side
        # AND connectivity is known healthy AND the failure is not a tool-registry
        # wedge (those have their own bounded retry path). The "no output despite
        # healthy connection" signal is what distinguishes a temporarily
        # unavailable agent (e.g. out of credits) from a transport or offline
        # failure. AgentInactivityTimeoutError subclasses AgentInvocationError,
        # so both class names are accepted here. We match by name rather than
        # isinstance to avoid a circular import through ralph.agents.invoke.
        #
        # The 2-element ``_WATCHDOG_UNAVAILABILITY_REASONS`` frozenset is
        # the canonical 2-element set; the conditional
        # ``child_alive in (False, None)`` for ``no_progress_quiet``
        # preserves backward-compat for ``child_alive=None`` and the
        # new ``child_alive=False`` paths (both Rule 2: exponential
        # backoff). The ``child_alive=True`` case falls through the
        # conditional and ``is_unavailable=False`` (Rule 1: same-agent
        # retry, defense-in-depth).
        is_unavailable = (
            category == FailureCategory.AGENT
            and (connectivity_state or "").casefold() == "online"
            and not reset_tool_registry
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
        # branch wins before any text scanning. The chain is walked
        # recursively (with a cycle guard) so the typed exception is
        # reachable when it is buried several layers deep in the
        # ``__cause__`` / ``__context__`` chain (e.g. when the
        # recovery layer wraps the watchdog's
        # ``_IdleStreamTimeoutError`` inside an
        # ``AgentInactivityTimeoutError`` whose ``__cause__`` is the
        # stream-timeout wrapper whose ``__cause__`` is the typed
        # ``IdleWatchdogKilledError``).
        if self._find_typed_watchdog_cause(exc) is not None:
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

    @staticmethod
    def _find_typed_watchdog_cause(
        exc: BaseException,
    ) -> IdleWatchdogKilledError | None:
        """Walk the ``__cause__`` / ``__context__`` chain for the watchdog typed exception.

        Returns the deepest ``IdleWatchdogKilledError`` reachable in the
        chain, or ``None`` if no typed cause is found. The walk is
        bounded by the visited-set (cycle guard) so a malformed cycle
        in the chain cannot hang the classifier.
        """
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
