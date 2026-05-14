from __future__ import annotations

from ralph.agents.activity import AgentActivityKind
from ralph.agents.execution_state import ClaudeInteractiveExecutionStrategy
from ralph.agents.parsers.claude_interactive import ClaudeInteractiveTranscriptParser
from ralph.display.vt_normalizer import normalize_vt_text


def test_vt_normalizer_strips_cursor_noise_but_keeps_semantic_text() -> None:
    raw = "\x1b[?25l\r\x1b[2Kclaude tool: read_file\n\x1b[?25h"

    normalized = normalize_vt_text(raw)

    assert normalized == "claude tool: read_file\n"


def test_interactive_parser_extracts_tool_use_from_tui_transcript() -> None:
    parser = ClaudeInteractiveTranscriptParser()

    events = parser.feed("claude tool: read_file\n")

    assert [event.kind for event in events] == ["tool_use"]
    assert events[0].text == "claude tool: read_file"


def test_interactive_parser_extracts_session_id_from_transcript_line() -> None:
    parser = ClaudeInteractiveTranscriptParser()

    events = parser.feed("Claude session ready. Session ID: pty-session-77\n")

    assert parser.session_id == "pty-session-77"
    assert [event.kind for event in events] == ["session"]


def test_claude_interactive_strategy_classifies_vt_tool_line_as_tool_use() -> None:
    strategy = ClaudeInteractiveExecutionStrategy()

    signal = strategy.classify_activity_line("\x1b[2K\rclaude tool: read_file\n")

    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_USE
