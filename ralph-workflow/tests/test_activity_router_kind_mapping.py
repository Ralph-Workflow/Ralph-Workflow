"""Tests verifying ActivityRouter kind mapping including THINKING."""

from __future__ import annotations

from ralph.display.activity_model import ActivityEventKind, ActivityProvider
from ralph.display.activity_router import ActivityRouter, _map_kind


def test_thinking_parser_type_maps_to_thinking_kind() -> None:
    assert _map_kind("thinking") is ActivityEventKind.THINKING


def test_text_maps_to_text_kind() -> None:
    assert _map_kind("text") is ActivityEventKind.TEXT


def test_tool_use_maps_correctly() -> None:
    assert _map_kind("tool_use") is ActivityEventKind.TOOL_USE


def test_unknown_maps_to_unknown() -> None:
    assert _map_kind("totally_unknown") is ActivityEventKind.UNKNOWN


def test_router_on_event_called_with_thinking_kind() -> None:
    events: list[tuple[str, ActivityEventKind, str | None, str | None]] = []

    def capture(
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_ref: str | None,
    ) -> None:
        events.append((unit_id, kind, content, raw_ref))

    router = ActivityRouter(on_event=capture)

    lines = [
        '{"type":"message_start","message":{"id":"msg-1"}}',
        (
            '{"type":"content_block_start","index":0,'
            '"content_block":{"type":"thinking","thinking":""}}'
        ),
        (
            '{"type":"content_block_delta","index":0,'
            '"delta":{"type":"thinking_delta","thinking":"I ponder"}}'
        ),
        '{"type":"content_block_stop","index":0}',
        '{"type":"message_stop"}',
    ]
    for line in lines:
        router.push_raw_line("u", line, provider=ActivityProvider.CLAUDE)

    thinking_events = [e for e in events if e[1] is ActivityEventKind.THINKING]
    assert thinking_events, f"Expected THINKING events, got: {events}"
    assert thinking_events[0][2] == "I ponder"


def test_router_on_event_not_called_when_none() -> None:
    router = ActivityRouter()
    # Should not raise; no on_event registered
    router.push_raw_line(
        "u",
        '{"type":"content_block_delta","delta":{"type":"text_delta","text":"hi"}}',
    )


def test_prefixed_claude_text_line_routes_as_content() -> None:
    """claude: <text> prefixed lines are parsed as text events, not JSON errors."""
    events: list[tuple[str, ActivityEventKind, str | None, str | None]] = []

    def capture(
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_ref: str | None,
    ) -> None:
        events.append((unit_id, kind, content, raw_ref))

    router = ActivityRouter(on_event=capture)
    router.push_raw_line(
        "u",
        "claude: hello from prefixed transcript",
        provider=ActivityProvider.CLAUDE,
    )

    text_events = [e for e in events if e[1] is ActivityEventKind.TEXT]
    error_events = [e for e in events if e[1] is ActivityEventKind.ERROR]
    assert text_events, f"Expected TEXT events, got: {events}"
    assert not error_events, f"Expected no ERROR events for prefixed line, got: {error_events}"
    assert text_events[0][2] == "hello from prefixed transcript"


def test_prefixed_claude_tool_line_routes_as_tool_use() -> None:
    """claude tool: <name> prefixed lines are parsed as tool_use events."""
    events: list[tuple[str, ActivityEventKind, str | None, str | None]] = []

    def capture(
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_ref: str | None,
    ) -> None:
        events.append((unit_id, kind, content, raw_ref))

    router = ActivityRouter(on_event=capture)
    router.push_raw_line("u", "claude tool: bash (ls -la)", provider=ActivityProvider.CLAUDE)

    tool_events = [e for e in events if e[1] is ActivityEventKind.TOOL_USE]
    error_events = [e for e in events if e[1] is ActivityEventKind.ERROR]
    assert tool_events, f"Expected TOOL_USE events, got: {events}"
    assert not error_events, f"Expected no ERROR events for tool line, got: {error_events}"
