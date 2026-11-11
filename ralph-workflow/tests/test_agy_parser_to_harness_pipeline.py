from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.agents.parsers.agy import AgyParser
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.pipeline.plumbing.smoke_plumbing import (
    _meaningful_output_lines,
)

_FIXTURE = Path(__file__).parent / "display" / "_fixtures" / "agy_wire_v1_1_13.jsonl"
_WHITELIST = {"text", "thinking", "tool_use", "tool_result", "error"}

pytestmark = pytest.mark.smoke


def _fixture_lines() -> list[str]:
    return _FIXTURE.read_text(encoding="utf-8").splitlines()


def _agy_config() -> AgentConfig:
    return AgentConfig(cmd="agy", transport=AgentTransport.AGY)


def test_v1_1_13_fixture_replay_emits_the_measured_five_event_types() -> None:
    events = list(AgyParser().parse(iter(_fixture_lines())))
    types = [event.type for event in events]
    for expected in ("text", "tool_use", "tool_result", "lifecycle", "stop"):
        assert expected in types, f"{expected!r} missing from {types}"


def test_v1_1_13_fixture_flows_through_meaningful_output_lines() -> None:
    lines = _fixture_lines()
    events = list(AgyParser().parse(iter(lines)))
    meaningful = _meaningful_output_lines(_agy_config(), lines)

    assert meaningful, "fixture text/tool content must survive the whitelist"
    prefixes: list[str] = []
    for entry in meaningful:
        prefix, sep, content = entry.partition(": ")
        assert sep, f"line lacks '<type>: <content>' shape: {entry!r}"
        assert prefix in _WHITELIST, f"non-whitelist prefix {prefix!r}"
        assert content, f"empty content in {entry!r}"
        prefixes.append(prefix)

    expected_prefixes = [
        event.type for event in events if event.type in _WHITELIST and event.content.strip()
    ]
    expected_semantic_lines = [
        f"{event.type}: {event.content}"
        for event in events
        if event.type in _WHITELIST and event.content.strip()
    ]
    assert len(expected_semantic_lines) > 8
    assert len(meaningful) == 8
    assert meaningful == expected_semantic_lines[:8]
    assert expected_semantic_lines[8:]
    assert prefixes == expected_prefixes[: len(prefixes)]
    assert 0 < len(prefixes) <= len(expected_prefixes)
    assert "lifecycle" not in prefixes
    assert "stop" not in prefixes


def test_synthetic_fenced_system_message_highlight_stays_off_meaningful_lines() -> None:
    frame = {
        "event": "step_update",
        "step_update": {
            "conversation_id": "synthetic",
            "step_index": 4,
            "state": "DONE",
            "step_type": "system_message",
            "text": "Here is the snippet:\n```python\nprint('hello')\n```\n",
        },
    }
    raw = json.dumps(frame)
    events = list(AgyParser().parse(iter([raw])))
    text_events = [event for event in events if event.type == "text"]
    assert len(text_events) == 1, f"expected one text event, got {events}"
    event = text_events[0]
    assert event.metadata is not None
    assert event.metadata.get("syntax_highlight") is True
    assert event.metadata.get("language") == "python"

    meaningful = _meaningful_output_lines(_agy_config(), [raw])
    assert len(meaningful) == 1
    assert meaningful[0].startswith("text: ")
    assert "syntax_highlight" not in meaningful[0]
