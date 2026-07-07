"""Tests for Nanocoder interactive output parsing."""

from __future__ import annotations

from ralph.agents.parsers import NanocoderParser, get_parser, resolve_parser_key
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.display.activity_provider import ActivityProvider, provider_for_transport
from ralph.display.activity_router import detect_provider_from_command


def test_nanocoder_parser_suppresses_control_only_tui_frames() -> None:
    parser = NanocoderParser()

    results = list(parser.parse(iter(["\x1b[?25l\r\x1b[2K   "])))

    assert results == []


def test_nanocoder_parser_coalesces_repeated_tui_status_frames() -> None:
    parser = NanocoderParser()

    results = list(
        parser.parse(
            iter(
                [
                    "⏵⏵⏵ yolo mode on · tune: full (auto) · ctx: 4%",
                    "⏵⏵⏵ yolo mode on · tune: full (auto) · ctx: 5%",
                    "[claude turn boundary]",
                ]
            )
        )
    )

    assert [(line.type, line.content, line.metadata) for line in results] == [
        ("status", "interactive output", {"event": "interactive_tui"})
    ]


def test_nanocoder_parser_keeps_plain_tool_marker_detection() -> None:
    parser = NanocoderParser()
    ansi_line = "\x1b[36m[plain] tool: ralph_submit_artifact\x1b[0m"

    results = list(parser.parse(iter([ansi_line])))

    assert [(line.type, line.content) for line in results] == [
        ("tool_use", "ralph_submit_artifact")
    ]


def test_nanocoder_transport_resolves_nanocoder_parser() -> None:
    key = resolve_parser_key("nanocoder", JsonParserType.GENERIC, AgentTransport.NANOCODER)

    assert key == "nanocoder"
    assert isinstance(get_parser(key), NanocoderParser)


def test_nanocoder_activity_provider_is_first_class() -> None:
    assert provider_for_transport("nanocoder") is ActivityProvider.NANOCODER
    assert detect_provider_from_command(["nanocoder"]) is ActivityProvider.NANOCODER
