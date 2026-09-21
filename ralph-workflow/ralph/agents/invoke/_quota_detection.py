"""Distinguish provider quota failures from echoed user prompt text."""

from __future__ import annotations

from ralph.agents.execution_state import is_user_prompt_event_line
from ralph.agents.invoke._quota_exhausted_error import QuotaExhaustedError
from ralph.recovery.failure_classifier import (
    _SUBSCRIPTION_LIMIT_SUBSTRINGS,
    _is_subscription_limit_message,
)

_PROVIDER_FAILURE_OUTPUT_MARKERS = (
    "retriableerror:",
    "providererror:",
    '"type":"error"',
    '"type": "error"',
    '"error":',
    '"error": ',
)


def _is_provider_failure_output(line: str) -> bool:
    normalized = line.strip().casefold()
    bare_provider_failure = any(
        normalized.startswith(marker.casefold()) for marker in _SUBSCRIPTION_LIMIT_SUBSTRINGS
    )
    return (
        normalized.startswith(
            ("api quota", "error:", "retriableerror:", "providererror:", "resource_exhausted")
        )
        or bare_provider_failure
        or any(marker in normalized for marker in _PROVIDER_FAILURE_OUTPUT_MARKERS)
    )


def raise_if_quota_exhausted(
    agent_name: str,
    stderr_text: str,
    parsed_output: list[str] | None,
    *,
    include_output: bool,
) -> None:
    """Raise for provider quota evidence, excluding transport-owned user echoes."""
    del include_output
    output = parsed_output or []
    for line in stderr_text.splitlines() or [stderr_text]:
        if _is_subscription_limit_message([line]):
            raise QuotaExhaustedError(agent_name, line)
    for source in output:
        for line in source.splitlines() or [source]:
            if (
                not is_user_prompt_event_line(line)
                and _is_provider_failure_output(line)
                and _is_subscription_limit_message([line])
            ):
                raise QuotaExhaustedError(agent_name, line)


__all__ = ["raise_if_quota_exhausted"]
