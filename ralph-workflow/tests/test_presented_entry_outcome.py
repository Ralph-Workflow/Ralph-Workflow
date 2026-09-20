"""Regression coverage for tool-result outcome severity."""

from __future__ import annotations

import pytest

from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.activity_provider import ActivityProvider
from ralph.display.agent_activity_event import AgentActivityEvent
from ralph.display.presented_entry import build_presented_entry, outcome_is_failure


def _tool_result_severity(metadata: dict[str, object]) -> str:
    """Render a tool result through the public entry-builder seam."""
    return build_presented_entry(
        AgentActivityEvent(
            provider=ActivityProvider.GENERIC,
            kind=ActivityEventKind.TOOL_RESULT,
            metadata=metadata,
        ),
    ).severity


def test_presented_entry_regression_stderr_without_failure_signal_is_info() -> None:
    """S-1: diagnostic stderr on an exit-0 result is not a failure."""
    metadata = {"stderr": "warning: noisy but successful"}

    assert outcome_is_failure(metadata) is False
    assert _tool_result_severity(metadata) == "info"


@pytest.mark.parametrize(
    "metadata",
    [
        {"exit_code": 1},
        {"is_error": True},
        {"error": "execution failed"},
    ],
)
def test_presented_entry_regression_explicit_failure_signals_remain_errors(
    metadata: dict[str, object],
) -> None:
    """S-1: explicit command failure signals still render as errors."""
    assert outcome_is_failure(metadata) is True
    assert _tool_result_severity(metadata) == "error"
