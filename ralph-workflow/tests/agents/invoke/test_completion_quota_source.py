from __future__ import annotations

import pytest

from ralph.agents.invoke import raise_if_quota_exhausted
from ralph.agents.invoke._quota_detection import is_provider_quota_output_line
from ralph.agents.invoke._quota_exhausted_error import QuotaExhaustedError


def test_completion_quota_check_ignores_agent_output_echoes() -> None:
    raise_if_quota_exhausted(
        "pi",
        "",
        ["I investigated RESOURCE_EXHAUSTED (code 429) and documented the fix."],
        include_output=False,
    )


def test_live_quota_detection_requires_provider_failure_shape() -> None:
    assert not is_provider_quota_output_line(
        "I documented that an Individual quota reached error occurred in a prior run."
    )
    assert is_provider_quota_output_line(
        "RetriableError: [resource_exhausted] Error; quota or rate limit is exhausted"
    )
    raise_if_quota_exhausted(
        "cursor",
        "",
        ["I investigated why the prior agent hit its quota exhausted condition."],
        include_output=True,
    )
    raise_if_quota_exhausted(
        "cursor",
        "",
        ["Individual quota reached in a scenario I investigated."],
        include_output=False,
    )


def test_completion_quota_check_accepts_provider_stderr() -> None:
    with pytest.raises(QuotaExhaustedError):
        raise_if_quota_exhausted(
            "pi",
            "RESOURCE_EXHAUSTED (code 429)",
            [],
            include_output=False,
        )


def test_completion_quota_check_accepts_explicit_rc_zero_provider_failure() -> None:
    with pytest.raises(QuotaExhaustedError):
        raise_if_quota_exhausted(
            "cursor",
            "",
            ["RetriableError: [resource_exhausted] Error; quota or rate limit is exhausted"],
            include_output=True,
        )


def test_completion_quota_check_keeps_nested_user_metadata_in_provider_error() -> None:
    with pytest.raises(QuotaExhaustedError):
        raise_if_quota_exhausted(
            "cursor",
            "",
            [
                '{"type":"error","context":{"type":"user"},'
                '"message":"RESOURCE_EXHAUSTED (code 429)"}'
            ],
            include_output=True,
        )


def test_completion_quota_check_accepts_bare_rc_zero_provider_failure() -> None:
    for failure in (
        "RESOURCE_EXHAUSTED (code 429)",
        "rate limit reached",
        "Individual quota reached",
        "API quota exhausted",
    ):
        with pytest.raises(QuotaExhaustedError):
            raise_if_quota_exhausted(
                "cursor",
                "",
                [failure],
                include_output=True,
            )
