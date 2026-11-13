from __future__ import annotations

from ralph.agents.invoke._quota_exhausted_error import QuotaExhaustedError
from ralph.agents.parsers.content_channel import (
    ContentChannel,
    ProviderText,
    _is_explicit_provider_failure_output,
    _is_provider_error_event,
    _is_provider_failure_output,
    _parse_json_event,
    channel_of,
    provider_evidence_text,
    provider_text,
)
from ralph.recovery.failure_classifier import (
    _SUBSCRIPTION_LIMIT_SUBSTRINGS,
    _is_subscription_limit_message,
)


def provider_quota_line_matches(text: ProviderText) -> bool:
    line = text.text
    normalized = line.strip().casefold()
    return (
        _is_explicit_provider_failure_output(line)
        or any(normalized == marker.casefold() for marker in _SUBSCRIPTION_LIMIT_SUBSTRINGS)
    ) and _is_subscription_limit_message([line])


def provider_quota_kill_evidence(text: ProviderText) -> bool:
    event = _parse_json_event(text.text)
    return event is None or _is_provider_error_event(event)


def provider_quota_stderr_line_matches(text: ProviderText) -> bool:
    return _is_subscription_limit_message([text.text])


def provider_quota_log_detail_matches(detail: str) -> bool:
    return any(
        _is_subscription_limit_message([line])
        for line in detail.splitlines()
        if line.strip() and channel_of(line) is ContentChannel.PROVIDER
    )


def provider_quota_output_line_matches(text: ProviderText, *, include_output: bool) -> bool:
    line = text.text
    return (
        _is_provider_failure_output(line)
        and (include_output or _is_explicit_provider_failure_output(line))
        and _is_subscription_limit_message([line])
    )


def is_quota_kill_evidence(detail: str) -> bool:
    text = provider_evidence_text(detail)
    return text is not None and provider_quota_kill_evidence(text)


def is_provider_quota_output_line(line: str) -> bool:
    text = provider_text(line)
    return text is not None and provider_quota_line_matches(text)


def raise_if_quota_exhausted(
    agent_name: str,
    stderr_text: str,
    parsed_output: list[str] | None,
    *,
    include_output: bool,
) -> None:
    output = parsed_output or []
    for line in stderr_text.splitlines() or [stderr_text]:
        text = provider_evidence_text(line)
        if text is not None and provider_quota_stderr_line_matches(text):
            raise QuotaExhaustedError(agent_name, line)
    for source in output:
        for line in source.splitlines() or [source]:
            text = provider_text(line)
            if text is not None and provider_quota_output_line_matches(
                text, include_output=include_output
            ):
                raise QuotaExhaustedError(agent_name, line)


__all__ = [
    "is_provider_quota_output_line",
    "is_quota_kill_evidence",
    "provider_quota_kill_evidence",
    "provider_quota_line_matches",
    "provider_quota_log_detail_matches",
    "provider_quota_output_line_matches",
    "provider_quota_stderr_line_matches",
    "raise_if_quota_exhausted",
]
