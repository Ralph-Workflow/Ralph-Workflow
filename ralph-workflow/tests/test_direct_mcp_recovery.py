from __future__ import annotations

from contextlib import suppress

import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.agents.invoke._direct_mcp_recovery import (
    iter_with_direct_mcp_recovery,
    run_with_direct_mcp_recovery,
)
from ralph.agents.invoke._session import extract_transport_session_id


def test_direct_mcp_recovery_ignores_nested_tool_payload_session_ids() -> None:
    calls: list[str | None] = []

    failure = AgentInvocationError(
        "claude",
        1,
        "Model returned an empty response with no tool calls",
        parsed_output=[
            '{"type":"tool_result","content":{"session_id":"tool-payload"}}',
        ],
    )

    def _run_attempt(session_id: str | None) -> str:
        calls.append(session_id)
        raise failure

    with suppress(AgentInvocationError):
        run_with_direct_mcp_recovery(
            lambda session_id, _capture: _run_attempt(session_id),
            max_retries=1,
            reset_tool_registry=lambda: None,
        )

    assert calls == [None, None]


def test_session_extractors_accept_completion_marker_session_id() -> None:
    line = "Task declared complete: session_id=abc123, summary=done, timestamp=1"

    assert extract_transport_session_id((line,)) == "abc123"


def test_transport_session_extractor_ignores_generic_plain_text_session_assignment() -> None:
    line = "assistant said session_id=fake-visible"

    assert extract_transport_session_id((line,)) is None


def test_transport_session_extractor_ignores_top_level_tool_payload_session_id() -> None:
    line = '{"type":"tool_result","session_id":"tool-payload"}'

    assert extract_transport_session_id((line,)) is None


# ``test_iter_direct_mcp_recovery_preserves_early_session_id_across_long_output``
# generates 500 fake log lines on the first attempt, then runs a recovery
# attempt. Under parallel xdist load the import path, the 500-line list
# materialization, and the second attempt together can exceed the 1s
# default test timeout enforced by ``tests/conftest.py`` even though the
# test itself is correct. The 5s cap is the documented minimum for
# non-trivial tests (see ``ralph/verify_timeout.py``) and is well under
# the 60s combined ``make verify`` budget. The 1s default policy is
# preserved globally; this marker only relaxes the cap for the specific
# test that needs extra wall-clock headroom.
@pytest.mark.timeout_seconds(5)
def test_iter_direct_mcp_recovery_preserves_early_session_id_across_long_output() -> None:
    calls: list[str | None] = []

    def _run_attempt(
        session_id: str | None,
    ) -> list[str]:
        calls.append(session_id)
        if len(calls) == 1:
            lines = ['{"type":"session","session_id":"sess-early"}']
            lines.extend(f"line-{index}" for index in range(500))

            def _iter() -> object:
                yield from lines
                raise AgentInvocationError(
                    "claude",
                    1,
                    "Model returned an empty response with no tool calls",
                    parsed_output=['{"type":"tool_result","tool":"read_file"}'],
                )

            return _iter()
        return ["recovered"]

    result = list(
        iter_with_direct_mcp_recovery(
            _run_attempt,
            max_retries=1,
            reset_tool_registry=lambda: None,
        )
    )

    assert result[-1] == "recovered"
    assert calls == [None, "sess-early"]


def test_direct_mcp_recovery_retries_stale_session_with_fresh_attempt() -> None:
    calls: list[str | None] = []

    def _run_attempt(session_id: str | None, capture: callable) -> str:
        calls.append(session_id)
        if len(calls) == 1:
            capture("sess-stale")
            raise AgentInvocationError(
                "claude",
                1,
                "No conversation found with session ID: sess-stale",
            )
        return "fresh-retry"

    result = run_with_direct_mcp_recovery(
        _run_attempt,
        max_retries=1,
        reset_tool_registry=lambda: None,
    )

    assert result == "fresh-retry"
    assert calls == [None, None]


def test_iter_direct_mcp_recovery_reports_observed_session_ids() -> None:
    observed_session_ids: list[str] = []

    result = list(
        iter_with_direct_mcp_recovery(
            lambda _session_id: ['{"type":"session","session_id":"sess-stream"}', "ok"],
            max_retries=0,
            reset_tool_registry=lambda: None,
            on_session_observed=observed_session_ids.append,
        )
    )

    assert result == ['{"type":"session","session_id":"sess-stream"}', "ok"]
    assert observed_session_ids == ["sess-stream"]
