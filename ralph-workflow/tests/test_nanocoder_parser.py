"""Tests for Nanocoder interactive output parsing."""

from __future__ import annotations

from ralph.agents.parsers import NanocoderParser, get_parser, resolve_parser_key
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig
from ralph.display.activity_provider import ActivityProvider, provider_for_transport
from ralph.display.activity_router import detect_provider_from_command
from ralph.pipeline.activity_stream import stream_parsed_agent_activity


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
        (
            "status",
            "⏵⏵⏵ yolo mode on · tune: full (auto) · ctx: 4%",
            {"event": "interactive_tui"},
        )
    ]


def test_nanocoder_parser_surfaces_visible_tui_snapshot() -> None:
    parser = NanocoderParser()

    results = list(
        parser.parse(
            iter(
                [
                    "\x1b[2K⏵⏵⏵ yolo mode on · tune: full (auto) · ctx: 4%",
                ]
            )
        )
    )

    assert [(line.type, line.content, line.metadata) for line in results] == [
        (
            "status",
            "⏵⏵⏵ yolo mode on · tune: full (auto) · ctx: 4%",
            {"event": "interactive_tui"},
        )
    ]


def test_nanocoder_parser_surfaces_distinct_visible_tui_snapshots() -> None:
    parser = NanocoderParser()

    results = list(
        parser.parse(
            iter(
                [
                    "⏵⏵⏵ yolo mode on · tune: full (auto) · ctx: 4%",
                    "Reading PROMPT.md and preparing the todo list.",
                    "Wrote tmp/interactive-nanocoder-smoke/todo-list.js",
                ]
            )
        )
    )

    assert [(line.type, line.content) for line in results] == [
        ("status", "⏵⏵⏵ yolo mode on · tune: full (auto) · ctx: 4%"),
        ("status", "Reading PROMPT.md and preparing the todo list."),
        ("status", "Wrote tmp/interactive-nanocoder-smoke/todo-list.js"),
    ]


def test_nanocoder_parser_surfaces_model_text_after_spinner_noise() -> None:
    parser = NanocoderParser()

    results = list(
        parser.parse(
            iter(
                [
                    "⠙ Waiting for chat to complete...",
                    "⠹ Waiting for chat to complete...",
                    "⠸ Waiting for chat to complete...",
                    "Syntax is valid. Now I'll submit the artifact with the exact content schema",
                    "specified:",
                ]
            )
        )
    )

    assert ("text", "Syntax is valid. Now I'll submit the artifact with the exact content schema") in [
        (line.type, line.content) for line in results
    ]
    assert ("text", "specified:") in [(line.type, line.content) for line in results]


def test_nanocoder_parser_classifies_executed_mcp_tool_line() -> None:
    parser = NanocoderParser()

    results = list(parser.parse(iter(["⚒ Executed mcp__ralph__ralph_submit_artifact × 1"])))

    assert [(line.type, line.content) for line in results] == [
        ("tool_use", "mcp__ralph__ralph_submit_artifact")
    ]


def test_nanocoder_activity_stream_renders_visible_tui_snapshot() -> None:
    rendered: list[str] = []
    config = AgentConfig(
        cmd="nanocoder",
        json_parser=JsonParserType.GENERIC,
        transport=AgentTransport.NANOCODER,
    )

    stream_parsed_agent_activity(
        ["\x1b[2K⏵⏵⏵ yolo mode on · tune: full (auto) · ctx: 4%"],
        parser_type=str(JsonParserType.GENERIC),
        agent_name="nanocoder",
        rendered_output_sink=rendered,
        agent_config=config,
    )

    assert rendered == ["nanocoder: ⏵⏵⏵ yolo mode on · tune: full (auto) · ctx: 4%"]


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
