"""Failure classification logic and rules."""

from __future__ import annotations

import errno
import socket
import urllib.error

from loguru import logger

from .classified_failure import ClassifiedFailure
from .failure_category import FailureCategory
from .failure_details import contains_casefolded_marker, failure_detail_parts

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

# Claude Code documented usage/billing limit messages that indicate the current
# Claude run cannot continue without waiting for reset time or adding usage.
_SUBSCRIPTION_LIMIT_SUBSTRINGS: tuple[str, ...] = (
    "You've hit your session limit",
    "You've hit your weekly limit",
    "You've hit your Opus limit",
    "Credit balance is too low",
    "You're out of extra usage",
    "Extra usage is required for long context requests",
    "workspace API usage limits",
    "billing_error",
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


def _is_subscription_limit_message(detail_parts: tuple[str, ...] | list[str]) -> bool:
    """Return True if the message matches Claude Code documented limit/billing families."""
    return contains_casefolded_marker(detail_parts, _SUBSCRIPTION_LIMIT_SUBSTRINGS)


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
        )

    def _categorize_exc(
        self,
        exc: BaseException,
        raw_message: str,
        detail_parts: list[str],
        *,
        connectivity_state: str | None,
    ) -> tuple[FailureCategory, bool, bool] | None:
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
        if _is_subscription_limit_message(detail_parts):
            return FailureCategory.AGENT, True, False
        if _is_suspicious_timeout_without_output(detail_parts, connectivity_state):
            return FailureCategory.AGENT, True, False
        msg_lower = raw_message.lower()
        if not _message_looks_environmental(raw_message) and (
            "empty" in msg_lower or "no output" in msg_lower or "timed out" in msg_lower
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
