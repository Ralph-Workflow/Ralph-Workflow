from __future__ import annotations

import json

import pytest

from ralph.agents.invoke import (
    is_provider_quota_output_line,
    is_quota_kill_evidence,
    provider_quota_kill_evidence,
    provider_quota_line_matches,
)
from ralph.agents.parsers.content_channel import (
    ContentChannel,
    ProviderText,
    channel_of,
    provider_evidence_text,
    provider_text,
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "provider_evidence_text none-check replaced with kill-evidence verdict": (
        "provider-originated malformed or unknown JSON on the stderr and agy-log "
        "paths must still reach phrase code, so provider_evidence_text now yields "
        "ProviderText for any envelope whose declared kind is not a recognized "
        "non-provider channel; the not-kill-evidence guarantee is pinned "
        "behaviourally by the provider_quota_kill_evidence and "
        "is_quota_kill_evidence assertions"
    ),
}

_QUOTA_WORDS = "Your account has credits exhausted for this period."

_USER_PROMPT_LINE = json.dumps(
    {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": "please continue"}]},
    }
)

_TOOL_RESULT_LINES = (
    json.dumps({"type": "tool_result", "error": "quota exceeded in fixture", "output": "x"}),
)

_USER_ROLE_TOOL_RESULT_LINE = json.dumps(
    {
        "type": "user",
        "message": {
            "content": [{"type": "tool_result", "content": "RESOURCE_EXHAUSTED (code 429)"}]
        },
    }
)

_QUOTA_ERROR_TEXT = "RESOURCE_EXHAUSTED (code 429)"

_TOOL_CALL_QUOTA_ERROR_LINES = (
    json.dumps({"type": "function_call", "name": "bash", "error": _QUOTA_ERROR_TEXT}),
    json.dumps(
        {"type": "mcp_tool_call", "tool": "read_multiple_files", "error": _QUOTA_ERROR_TEXT}
    ),
    json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "function_call", "name": "bash", "error": _QUOTA_ERROR_TEXT},
        }
    ),
    json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "tool": "read_multiple_files",
                "error": _QUOTA_ERROR_TEXT,
            },
        }
    ),
)

_TOOL_CALL_LINES = (
    json.dumps(
        {"type": "tool_use", "name": "bash", "input": {"command": "echo rate limit reached"}}
    ),
    json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "tool": "read_multiple_files",
                "status": "completed",
                "result": {"content": [{"type": "text", "text": _QUOTA_WORDS}]},
            },
        }
    ),
)

_REASONING_LINES = (
    json.dumps({"type": "item.completed", "item": {"type": "reasoning", "text": _QUOTA_WORDS}}),
)

_NON_PROVIDER_LINES = (
    json.dumps({"error": None, "content": _QUOTA_WORDS}),
    '{"error":null,"content":"' + _QUOTA_WORDS + '"}',
    json.dumps({"type": "thinking", "text": _QUOTA_WORDS}),
    json.dumps({"type": "subagent", "text": _QUOTA_WORDS}),
    "{not json",
)


@pytest.mark.parametrize(
    "line",
    [
        *_TOOL_RESULT_LINES,
        _USER_ROLE_TOOL_RESULT_LINE,
        *_TOOL_CALL_LINES,
        *_REASONING_LINES,
        *_NON_PROVIDER_LINES,
    ],
)
def test_tool_and_reasoning_content_is_never_provider_channel(line: str) -> None:
    assert channel_of(line) is not ContentChannel.PROVIDER
    assert provider_text(line) is None
    assert not is_provider_quota_output_line(line)


@pytest.mark.parametrize("line", _TOOL_CALL_LINES)
def test_tool_call_events_map_to_tool_call_channel(line: str) -> None:
    assert channel_of(line) is ContentChannel.TOOL_CALL


@pytest.mark.parametrize("line", _TOOL_CALL_QUOTA_ERROR_LINES)
def test_tool_call_quota_error_property_is_never_provider_output(line: str) -> None:
    assert channel_of(line) is ContentChannel.TOOL_CALL
    assert provider_text(line) is None
    assert not is_provider_quota_output_line(line)
    assert not is_quota_kill_evidence(line)


@pytest.mark.parametrize("line", _TOOL_RESULT_LINES)
def test_tool_result_events_map_to_tool_result_channel(line: str) -> None:
    assert channel_of(line) is ContentChannel.TOOL_RESULT


def test_user_role_message_carrying_tool_result_maps_to_user_channel() -> None:
    assert channel_of(_USER_ROLE_TOOL_RESULT_LINE) is ContentChannel.USER
    assert provider_text(_USER_ROLE_TOOL_RESULT_LINE) is None
    assert not is_provider_quota_output_line(_USER_ROLE_TOOL_RESULT_LINE)


@pytest.mark.parametrize("line", _REASONING_LINES)
def test_reasoning_events_map_to_reasoning_channel(line: str) -> None:
    assert channel_of(line) is ContentChannel.REASONING


_PROVIDER_ERROR_LINES = (
    json.dumps({"type": "error", "message": "RESOURCE_EXHAUSTED (code 429)"}),
    json.dumps({"error": {"message": "Individual quota reached"}}),
    json.dumps({"type": "result", "error": "rate limit reached"}),
    json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "error", "message": "You exceeded your current quota"},
        }
    ),
    "RESOURCE_EXHAUSTED (code 429)",
    "RetriableError: [resource_exhausted] Error; quota or rate limit is exhausted",
)


@pytest.mark.parametrize("line", _PROVIDER_ERROR_LINES)
def test_provider_error_lines_map_to_provider_channel(line: str) -> None:
    assert channel_of(line) is ContentChannel.PROVIDER
    text = provider_text(line)
    assert isinstance(text, ProviderText)
    assert text.text == line


@pytest.mark.parametrize("line", [json.dumps({"error": None, "content": "ok"})])
def test_error_null_is_not_a_provider_error(line: str) -> None:
    assert provider_text(line) is None
    assert not is_provider_quota_output_line(line)


def test_user_prompt_line_maps_to_user_channel() -> None:
    assert channel_of(_USER_PROMPT_LINE) is ContentChannel.USER
    assert provider_text(_USER_PROMPT_LINE) is None
    assert not is_provider_quota_output_line(_USER_PROMPT_LINE)


_USER_MESSAGE_EVENT_LINES = (
    json.dumps(
        {"type": "message_start", "message": {"role": "user", "content": "credits exhausted"}}
    ),
    json.dumps(
        {"type": "message_end", "message": {"role": "user", "content": "credits exhausted"}}
    ),
)


@pytest.mark.parametrize("line", _USER_MESSAGE_EVENT_LINES)
def test_user_message_envelope_events_are_never_provider_evidence(line: str) -> None:
    assert channel_of(line) is ContentChannel.USER
    assert provider_text(line) is None
    assert provider_evidence_text(line) is None
    assert not is_provider_quota_output_line(line)
    assert not is_quota_kill_evidence(line)


def test_non_json_plain_text_maps_to_user_channel_not_provider() -> None:
    assert channel_of("just a status update") is ContentChannel.USER
    assert provider_text("just a status update") is None


_QUOTA_PROVIDER_ERROR_LINES = (
    "RESOURCE_EXHAUSTED (code 429)",
    json.dumps({"type": "error", "message": "RESOURCE_EXHAUSTED (code 429)"}),
    json.dumps({"type": "result", "error": "RESOURCE_EXHAUSTED (code 429)"}),
)


@pytest.mark.parametrize("line", _QUOTA_PROVIDER_ERROR_LINES)
def test_provider_quota_errors_still_match_quota_detection(line: str) -> None:
    text = provider_text(line)
    assert isinstance(text, ProviderText)
    assert provider_quota_line_matches(text)
    assert is_provider_quota_output_line(line)
    assert provider_quota_kill_evidence(text)
    assert is_quota_kill_evidence(line)


@pytest.mark.parametrize("line", _PROVIDER_ERROR_LINES)
def test_cores_agree_with_str_boundaries_on_provider_lines(line: str) -> None:
    text = provider_text(line)
    assert text is not None
    assert provider_quota_line_matches(text) == is_provider_quota_output_line(line)
    assert provider_quota_kill_evidence(text) == is_quota_kill_evidence(line)


@pytest.mark.parametrize(
    "line",
    [
        *_TOOL_RESULT_LINES,
        _USER_ROLE_TOOL_RESULT_LINE,
        *_TOOL_CALL_LINES,
        *_TOOL_CALL_QUOTA_ERROR_LINES,
        *_REASONING_LINES,
        *_NON_PROVIDER_LINES,
    ],
)
def test_cores_agree_with_str_boundaries_on_non_provider_lines(line: str) -> None:
    assert provider_text(line) is None
    assert not is_provider_quota_output_line(line)


_KILL_EVIDENCE_LINES = (
    "RESOURCE_EXHAUSTED (code 429)",
    "some unrelated plain text",
)


@pytest.mark.parametrize("detail", _KILL_EVIDENCE_LINES)
def test_non_json_detail_counts_as_kill_evidence(detail: str) -> None:
    text = provider_evidence_text(detail)
    assert isinstance(text, ProviderText)
    assert provider_quota_kill_evidence(text)
    assert is_quota_kill_evidence(detail)


@pytest.mark.parametrize(
    "line",
    [
        *_TOOL_RESULT_LINES,
        _USER_ROLE_TOOL_RESULT_LINE,
        *_TOOL_CALL_QUOTA_ERROR_LINES,
        *_NON_PROVIDER_LINES,
    ],
)
def test_json_non_provider_detail_is_not_kill_evidence(line: str) -> None:
    text = provider_evidence_text(line)
    assert text is None or not provider_quota_kill_evidence(text)
    assert not is_quota_kill_evidence(line)


def test_provider_text_is_frozen_value_type() -> None:
    text = provider_text("RESOURCE_EXHAUSTED (code 429)")
    assert text is not None
    assert text == ProviderText(text="RESOURCE_EXHAUSTED (code 429)")
    assert hash(text) == hash(ProviderText(text="RESOURCE_EXHAUSTED (code 429)"))
    assert not hasattr(text, "__dict__")
