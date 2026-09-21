from __future__ import annotations

import pytest

from ralph.agents.invoke._completion import _raise_if_quota_exhausted
from ralph.agents.invoke._quota_exhausted_error import QuotaExhaustedError


def test_completion_quota_check_ignores_agent_output_echoes() -> None:
    _raise_if_quota_exhausted(
        "pi",
        "",
        ['{"type":"message_start","text":"quota or rate limit is exhausted"}'],
    )


def test_completion_quota_check_accepts_provider_stderr() -> None:
    with pytest.raises(QuotaExhaustedError):
        _raise_if_quota_exhausted("pi", "RESOURCE_EXHAUSTED (code 429)", [])
