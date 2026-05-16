"""Unit tests for the typed activity model contract."""

from __future__ import annotations

from dataclasses import fields

from ralph.agents.parsers import AgentOutputLine
from ralph.display.activity_model import (
    ActivityEventKind,
    ActivityProvider,
    ActivityVisibilityHint,
    AgentActivityEvent,
)

_VISIBLE_WAITING_SEQUENCE = 3


def test_activity_event_kind_covers_canonical_cross_layer_events() -> None:
    assert ActivityEventKind.TEXT.value == "text"
    assert ActivityEventKind.STATUS.value == "status"
    assert ActivityEventKind.TOOL_USE.value == "tool_use"
    assert ActivityEventKind.TOOL_RESULT.value == "tool_result"
    assert ActivityEventKind.ERROR.value == "error"
    assert ActivityEventKind.LIFECYCLE.value == "lifecycle"
    assert ActivityEventKind.HEARTBEAT.value == "heartbeat"
    assert ActivityEventKind.PROGRESS.value == "progress"
    assert ActivityEventKind.UNKNOWN.value == "unknown"


def test_activity_event_supports_visible_waiting_state_without_blank_string_overload() -> None:
    event = AgentActivityEvent(
        provider=ActivityProvider.CLAUDE,
        kind=ActivityEventKind.STATUS,
        content=None,
        visibility=ActivityVisibilityHint.FALLBACK_ONLY,
        source="message_start",
        sequence=_VISIBLE_WAITING_SEQUENCE,
    )

    assert event.content is None
    assert event.visibility is ActivityVisibilityHint.FALLBACK_ONLY
    assert event.sequence == _VISIBLE_WAITING_SEQUENCE


def test_activity_event_preserves_source_and_structured_metadata() -> None:
    event = AgentActivityEvent(
        provider=ActivityProvider.OPENCODE,
        kind=ActivityEventKind.TOOL_USE,
        content="bash",
        metadata={"command": "pytest", "workdir": "/repo"},
        visibility=ActivityVisibilityHint.VISIBLE,
        source="tool_use.part",
        timestamp="2026-04-18T12:00:00Z",
    )

    assert event.provider is ActivityProvider.OPENCODE
    assert event.kind is ActivityEventKind.TOOL_USE
    assert event.metadata["command"] == "pytest"
    assert event.source == "tool_use.part"
    assert event.timestamp == "2026-04-18T12:00:00Z"


def test_activity_event_model_exposes_typed_contract_fields() -> None:
    field_names = {field.name for field in fields(AgentActivityEvent)}

    assert field_names == {
        "provider",
        "kind",
        "content",
        "metadata",
        "visibility",
        "source",
        "sequence",
        "timestamp",
    }


def test_agent_output_line_remains_available_as_legacy_parser_output() -> None:
    line = AgentOutputLine(type="text", content="hello")

    assert line.content == "hello"
    assert "legacy" in (AgentOutputLine.__doc__ or "").lower()
