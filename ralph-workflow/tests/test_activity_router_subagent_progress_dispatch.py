from __future__ import annotations

import json

from ralph.agents.parsers import AgentOutputLine
from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.activity_model import ActivityProvider
from ralph.display.activity_provider import provider_for_transport
from ralph.display.activity_router import (
    ActivityRouter,
    detect_provider_from_command,
)
from ralph.mcp.server._activity_sink import (
    get_subagent_sink,
    reset_subagent_sink,
    set_subagent_sink,
)


def test_router_emits_subagent_progress_for_tool_use_line() -> None:
    events: list[tuple[str, ActivityEventKind, str | None, str | None]] = []
    sink_calls: list[str] = []

    def _on_event(
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_ref: str | None,
        metadata: dict[str, object],
    ) -> None:
        events.append((unit_id, kind, content, raw_ref))

    def _sink(line: str) -> None:
        sink_calls.append(line)

    sink_token = set_subagent_sink(_sink)
    try:
        router = ActivityRouter(on_event=_on_event)
        tool_line = json.dumps(
            {
                "type": "content_block_start",
                "content_block": {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "ls"},
                },
            }
        )
        router.push_raw_line("u", tool_line, provider=ActivityProvider.CLAUDE)

        progress_events = [e for e in events if e[1] is ActivityEventKind.SUBAGENT_PROGRESS]
        assert len(progress_events) >= 1, (
            "ActivityRouter.push_raw_line MUST emit a SUBAGENT_PROGRESS"
            f" event for a tool_use line; events={events}"
        )
        assert progress_events[0][2] == "tool_use:Bash", (
            "SUBAGENT_PROGRESS event content MUST be the sanitized"
            f" 'tool_use:Bash' summary; got: {progress_events[0][2]!r}"
        )
        assert progress_events[0][0] == "u", (
            f"SUBAGENT_PROGRESS unit_id MUST be 'u'; got: {progress_events[0][0]!r}"
        )

        tool_use_sink_calls = [s for s in sink_calls if s.startswith("tool_use:")]
        assert tool_use_sink_calls, (
            "ActivityRouter.push_raw_line MUST forward the sanitized"
            " summary to invoke_subagent_sink so the watchdog's"
            f" record_subagent_work channel is refreshed; sink_calls={sink_calls}"
        )
        assert "tool_use:Bash" in tool_use_sink_calls, (
            f"SUBAGENT sink MUST receive 'tool_use:Bash'; got: {tool_use_sink_calls!r}"
        )
    finally:
        reset_subagent_sink(sink_token)


def test_router_emits_subagent_progress_for_text_line() -> None:
    events: list[tuple[str, ActivityEventKind, str | None, str | None]] = []

    def _on_event(
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_ref: str | None,
        metadata: dict[str, object],
    ) -> None:
        events.append((unit_id, kind, content, raw_ref))

    router = ActivityRouter(on_event=_on_event)
    router.push_raw_line(
        "u",
        json.dumps(
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "Hello world"},
            }
        ),
        provider=ActivityProvider.CLAUDE,
    )

    progress_events = [e for e in events if e[1] is ActivityEventKind.SUBAGENT_PROGRESS]
    assert progress_events, (
        "ActivityRouter.push_raw_line MUST emit a SUBAGENT_PROGRESS"
        f" event for text content_block_delta lines; events={events}"
    )
    assert any((content or "").startswith("text:") for _, _, content, _ in progress_events), (
        "SUBAGENT_PROGRESS summary for text lines MUST carry the"
        f" 'text:' prefix; progress_events={progress_events}"
    )


def test_router_subagent_progress_is_silent_when_parser_has_no_hook() -> None:
    events: list[tuple[str, ActivityEventKind, str | None, str | None]] = []
    sink_calls: list[str] = []

    def _on_event(
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_ref: str | None,
        metadata: dict[str, object],
    ) -> None:
        events.append((unit_id, kind, content, raw_ref))

    def _sink(line: str) -> None:
        sink_calls.append(line)

    class _StubParser:
        def parse(self, _lines: object) -> list[AgentOutputLine]:
            return [AgentOutputLine(type="tool_use", content="bash")]

    sink_token = set_subagent_sink(_sink)
    try:
        router = ActivityRouter(
            parser_factory=lambda _provider: _StubParser(),
            on_event=_on_event,
        )
        router.push_raw_line("u", "anything", provider=ActivityProvider.GENERIC)
        progress_events = [e for e in events if e[1] is ActivityEventKind.SUBAGENT_PROGRESS]
        assert not progress_events, (
            "Parsers without emit_subagent_activity MUST NOT emit"
            f" SUBAGENT_PROGRESS; events={events}"
        )
        assert sink_calls == [], (
            "Parsers without emit_subagent_activity MUST NOT invoke"
            f" the subagent sink; sink_calls={sink_calls}"
        )
    finally:
        reset_subagent_sink(sink_token)


def test_router_swallows_buggy_parser_hook_exception() -> None:
    events: list[tuple[str, ActivityEventKind, str | None, str | None]] = []

    def _on_event(
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_ref: str | None,
        metadata: dict[str, object],
    ) -> None:
        events.append((unit_id, kind, content, raw_ref))

    class _BuggyParser:
        def parse(self, _lines: object) -> list[AgentOutputLine]:
            return [AgentOutputLine(type="tool_use", content="bash")]

        def emit_subagent_activity(self, _line: AgentOutputLine, sink: object) -> None:
            raise RuntimeError("buggy hook")

    router = ActivityRouter(
        parser_factory=lambda _provider: _BuggyParser(),
        on_event=_on_event,
    )
    router.push_raw_line("u", "anything", provider=ActivityProvider.GENERIC)

    tool_use_events = [e for e in events if e[1] is ActivityEventKind.TOOL_USE]
    assert tool_use_events, (
        "Even when emit_subagent_activity raises, the router MUST still"
        f" emit the canonical TOOL_USE event; events={events}"
    )
    progress_events = [e for e in events if e[1] is ActivityEventKind.SUBAGENT_PROGRESS]
    assert not progress_events, (
        f"A parser hook that raised MUST NOT emit SUBAGENT_PROGRESS; events={events}"
    )


def test_router_subagent_progress_with_no_sink_registered() -> None:
    events: list[tuple[str, ActivityEventKind, str | None, str | None]] = []

    def _on_event(
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_ref: str | None,
        metadata: dict[str, object],
    ) -> None:
        events.append((unit_id, kind, content, raw_ref))

    saved_token = set_subagent_sink(None)
    try:
        assert get_subagent_sink() is None
        router = ActivityRouter(on_event=_on_event)
        tool_line = json.dumps(
            {
                "type": "content_block_start",
                "content_block": {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"command": "ls"},
                },
            }
        )
        router.push_raw_line("u", tool_line, provider=ActivityProvider.CLAUDE)
        progress_events = [e for e in events if e[1] is ActivityEventKind.SUBAGENT_PROGRESS]
        assert progress_events, (
            "SUBAGENT_PROGRESS event MUST be emitted via on_event even"
            f" when no subagent sink is registered; events={events}"
        )
    finally:
        reset_subagent_sink(saved_token)


def test_router_uses_claude_interactive_parser_for_claude_interactive_provider() -> None:
    events: list[tuple[str, ActivityEventKind, str | None, str | None]] = []

    def _on_event(
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_ref: str | None,
        metadata: dict[str, object],
    ) -> None:
        events.append((unit_id, kind, content, raw_ref))

    router = ActivityRouter(on_event=_on_event)
    router.push_raw_line(
        "u",
        "claude: hello from interactive transcript",
        provider=ActivityProvider.CLAUDE_INTERACTIVE,
    )
    error_events = [e for e in events if e[1] is ActivityEventKind.ERROR]
    assert not error_events, (
        "ActivityRouter.push_raw_line with CLAUDE_INTERACTIVE"
        " MUST route through ClaudeInteractiveParser (not GenericParser);"
        f" got error events: {error_events}"
    )


def test_router_uses_pi_parser_for_pi_provider() -> None:
    events: list[tuple[str, ActivityEventKind, str | None, str | None]] = []

    def _on_event(
        unit_id: str,
        kind: ActivityEventKind,
        content: str | None,
        raw_ref: str | None,
        metadata: dict[str, object],
    ) -> None:
        events.append((unit_id, kind, content, raw_ref))

    router = ActivityRouter(on_event=_on_event)
    router.push_raw_line(
        "u",
        '{"type": "session", "id": "sess-1"}',
        provider=ActivityProvider.PI,
    )
    error_events = [e for e in events if e[1] is ActivityEventKind.ERROR]
    assert not error_events, (
        "ActivityRouter.push_raw_line with PI MUST route through"
        f" PiParser (not GenericParser); got error events: {error_events}"
    )


def test_detect_provider_from_command_recognizes_pi() -> None:
    assert detect_provider_from_command(["pi"]) is ActivityProvider.PI
    assert detect_provider_from_command(["/usr/local/bin/pi"]) is ActivityProvider.PI
    assert detect_provider_from_command(["pi-mono"]) is ActivityProvider.PI


def test_detect_provider_from_command_recognizes_claude_interactive() -> None:
    assert (
        detect_provider_from_command(["claude-interactive"]) is ActivityProvider.CLAUDE_INTERACTIVE
    )
    assert (
        detect_provider_from_command(["claude_interactive"]) is ActivityProvider.CLAUDE_INTERACTIVE
    )
    assert detect_provider_from_command(["claude"]) is ActivityProvider.CLAUDE


def test_provider_for_transport_round_trips_supported_transports() -> None:
    assert provider_for_transport("claude") is ActivityProvider.CLAUDE
    assert provider_for_transport("claude_interactive") is ActivityProvider.CLAUDE_INTERACTIVE
    assert provider_for_transport("codex") is ActivityProvider.CODEX
    assert provider_for_transport("cursor") is ActivityProvider.CURSOR
    assert provider_for_transport("opencode") is ActivityProvider.OPENCODE
    assert provider_for_transport("gemini") is ActivityProvider.GEMINI
    assert provider_for_transport("agy") is ActivityProvider.AGY
    assert provider_for_transport("pi") is ActivityProvider.PI
    assert provider_for_transport("nanocoder") is ActivityProvider.NANOCODER
    assert provider_for_transport("generic") is ActivityProvider.GENERIC
    assert provider_for_transport(None) is ActivityProvider.GENERIC
