from __future__ import annotations

import json

import pytest

from ralph.agents.invoke import is_provider_quota_output_line
from ralph.agents.parsers.content_channel import ContentChannel, channel_of
from ralph.agents.parsers.line_channel import line_channel

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_parser_tagged_tool_result_line_yields_no_quota_evidence": (
        "assert is_provider_quota_output_line replaces private provider_quota_line_evidence helper"
    ),
    "test_parser_tagged_provider_error_line_yields_quota_evidence": (
        "assert is_provider_quota_output_line replaces private provider_quota_line_evidence helper"
    ),
    "test_unparsed_quota_text_still_detected_via_channel_of_fallback": (
        "assert is_provider_quota_output_line replaces private provider_quota_line_evidence helper"
    ),
}
_QUOTA_WORDS = "Your account has credits exhausted for this period."

_CODEX_PARSER_KEY = "codex"

_TOOL_RESULT_LINES = (
    json.dumps({"type": "tool_result", "error": "RESOURCE_EXHAUSTED (code 429)", "output": "x"}),
    json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "tool_result", "content": _QUOTA_WORDS},
        }
    ),
)

_PROVIDER_ERROR_LINES = (
    json.dumps({"type": "error", "message": "RESOURCE_EXHAUSTED (code 429)"}),
    json.dumps({"error": {"message": "Individual quota reached"}}),
    "RESOURCE_EXHAUSTED (code 429)",
    "RetriableError: [resource_exhausted] Error; quota or rate limit is exhausted",
)

_CODEX_UNPARSED_LINES = (
    "",
    json.dumps({"type": "text", "content": ""}),
)


@pytest.mark.parametrize("line", _TOOL_RESULT_LINES)
def test_parser_tagged_tool_result_line_maps_to_tool_result_channel(line: str) -> None:
    assert line_channel(_CODEX_PARSER_KEY, line) is ContentChannel.TOOL_RESULT


@pytest.mark.parametrize("line", _TOOL_RESULT_LINES)
def test_parser_tagged_tool_result_line_yields_no_quota_evidence(line: str) -> None:
    channel = line_channel(_CODEX_PARSER_KEY, line)
    assert channel is not ContentChannel.PROVIDER
    assert is_provider_quota_output_line(line) is False


@pytest.mark.parametrize("line", _PROVIDER_ERROR_LINES)
def test_parser_tagged_provider_error_line_maps_to_provider_channel(line: str) -> None:
    assert line_channel(_CODEX_PARSER_KEY, line) is ContentChannel.PROVIDER


@pytest.mark.parametrize("line", _PROVIDER_ERROR_LINES)
def test_parser_tagged_provider_error_line_yields_quota_evidence(line: str) -> None:
    channel = line_channel(_CODEX_PARSER_KEY, line)
    assert channel is ContentChannel.PROVIDER
    assert is_provider_quota_output_line(line) is True


@pytest.mark.parametrize("line", _CODEX_UNPARSED_LINES)
def test_line_the_parser_yields_nothing_for_falls_back_to_channel_of(line: str) -> None:
    assert line_channel(_CODEX_PARSER_KEY, line) == channel_of(line)


@pytest.mark.parametrize("line", (*_PROVIDER_ERROR_LINES, "plain agent narrative"))
def test_missing_parser_key_falls_back_to_channel_of(line: str) -> None:
    assert line_channel(None, line) == channel_of(line)


@pytest.mark.parametrize("line", (*_PROVIDER_ERROR_LINES, "plain agent narrative"))
def test_unregistered_parser_key_falls_back_to_channel_of(line: str) -> None:
    assert line_channel("no_such_parser", line) == channel_of(line)


def test_unregistered_parser_key_never_defaults_to_provider() -> None:
    assert line_channel("no_such_parser", "plain agent narrative") is ContentChannel.USER


def test_unparsed_quota_text_still_detected_via_channel_of_fallback() -> None:
    line = "RESOURCE_EXHAUSTED (code 429)"
    channel = line_channel(None, line)
    assert channel is ContentChannel.PROVIDER
    assert is_provider_quota_output_line(line) is True
