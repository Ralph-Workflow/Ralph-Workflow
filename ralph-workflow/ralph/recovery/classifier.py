"""Failure classification: categorize exceptions for intelligent attribution."""

from __future__ import annotations

import errno
import socket
from dataclasses import dataclass
from enum import StrEnum

from loguru import logger

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
    }
)


class FailureCategory(StrEnum):
    """Categories of pipeline failures for attribution and routing."""

    ENVIRONMENTAL = "environmental"
    AGENT = "agent"
    USER_CONFIG = "user_config"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class ClassifiedFailure:
    """A failure with its category, attribution, and budget-counting decision."""

    category: FailureCategory
    reason: str
    attributed_agent: str | None
    attributed_phase: str
    counts_against_budget: bool
    original_exception: BaseException | None
    raw_message: str


def _is_environmental_exc(exc: BaseException) -> bool:
    """Return True if this exception is clearly an environmental/network fault."""
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    if isinstance(exc, socket.gaierror):
        return True
    try:
        import urllib.error  # noqa: PLC0415

        if isinstance(exc, urllib.error.URLError):
            return True
    except ImportError:
        pass
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
    }


def _is_user_config_message(raw_message: str) -> bool:
    config_substrings = (
        "is not bound",
        "is not defined",
        "Checkpoint was saved at phase",
        "no longer exists in pipeline.toml",
    )
    return any(s in raw_message for s in config_substrings)


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
    ) -> ClassifiedFailure:
        """Classify a failure and return a ClassifiedFailure.

        Args:
            exc: The exception or string message to classify.
            phase: The pipeline phase where the failure occurred.
            agent: The agent name, if known.

        Returns:
            ClassifiedFailure with category, attribution, and budget decision.
        """
        if isinstance(exc, str):
            raw_message = exc
            original: BaseException | None = None
        else:
            exc_msg = str(exc)
            type_name = type(exc).__name__
            raw_message = f"{type_name}: {exc_msg}" if exc_msg else type_name
            original = exc

        exc_obj = exc if isinstance(exc, BaseException) else None
        category, counts = self._categorize(exc_obj, raw_message)

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
        )

    def _categorize_exc(
        self,
        exc: BaseException,
        raw_message: str,
    ) -> tuple[FailureCategory, bool] | None:
        """Try to categorize based on exception type. Returns None if uncategorized."""
        if _is_user_config_exc(exc):
            return FailureCategory.USER_CONFIG, False
        if _is_environmental_exc(exc):
            return FailureCategory.ENVIRONMENTAL, False
        type_name = type(exc).__name__
        if type_name == "AgentInactivityTimeoutError":
            return FailureCategory.AGENT, True
        if type_name == "AgentInvocationError":
            msg_lower = raw_message.lower()
            if not _message_looks_environmental(raw_message) and (
                "empty" in msg_lower
                or "no output" in msg_lower
                or "timed out" in msg_lower
            ):
                return FailureCategory.AGENT, True
        return None

    def _categorize(
        self,
        exc: BaseException | None,
        raw_message: str,
    ) -> tuple[FailureCategory, bool]:
        """Return (category, counts_against_budget) for a failure."""
        if exc is not None:
            result = self._categorize_exc(exc, raw_message)
            if result is not None:
                return result
        if _message_looks_environmental(raw_message):
            return FailureCategory.ENVIRONMENTAL, False
        if _is_user_config_message(raw_message):
            return FailureCategory.USER_CONFIG, False
        return FailureCategory.AMBIGUOUS, False

    def _build_reason(self, category: FailureCategory, raw_message: str) -> str:
        prefix_map = {
            FailureCategory.ENVIRONMENTAL: "Environmental fault",
            FailureCategory.AGENT: "Agent fault",
            FailureCategory.USER_CONFIG: "Configuration fault",
            FailureCategory.AMBIGUOUS: "Ambiguous fault (flagged for review)",
        }
        prefix = prefix_map.get(category, "Unknown fault")
        msg = raw_message[:300] if raw_message else "(no message)"
        return f"{prefix}: {msg}"


def is_retryable_without_budget(failure: ClassifiedFailure) -> bool:
    """Return True if this failure should retry without debiting the agent budget.

    Environmental and ambiguous failures retry without counting.
    Agent and user_config failures consume budget (user_config should not
    reach runtime, but defensively we treat them as non-retryable without budget).
    """
    return not failure.counts_against_budget
