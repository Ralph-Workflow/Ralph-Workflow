from __future__ import annotations

import json

from ralph.agents.invoke._pty_transcript import transcript_lines_from_event
from ralph.agents.parsers.claude_interactive import (
    ClaudeInteractiveParser,
    ClaudeInteractiveTranscriptParser,
)


def test_transcript_lines_from_session_event_emits_session_line() -> None:
    raw_line = json.dumps(
        {
            "type": "mode",
            "mode": "normal",
            "sessionId": "sess-transcript-1",
        }
    )

    lines = transcript_lines_from_event(raw_line)

    assert lines == ["Session ID: sess-transcript-1\n"]


def test_transcript_lines_from_assistant_tool_result_preserves_envelope() -> None:
    raw_line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_exec_1",
                        "content": [{"type": "text", "text": "Exit code: 0\nStdout:\nhello"}],
                    }
                ]
            },
        }
    )

    lines = transcript_lines_from_event(raw_line)

    assert lines == [f"{raw_line}\n"]


def test_transcript_lines_from_assistant_thinking_preserves_envelope() -> None:
    raw_line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "Let me compare these plan steps before editing.",
                    }
                ]
            },
        }
    )

    lines = transcript_lines_from_event(raw_line)

    assert lines == [f"{raw_line}\n"]


def test_claude_interactive_transcript_bridge_regression_preserves_tool_envelopes() -> None:
    """User-reported Claude smoke parsing regression, 2026-07-14."""
    transcript_parser = ClaudeInteractiveTranscriptParser()
    bridged_lines: list[str] = []
    events = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_read",
                        "name": "mcp__ralph__read_file",
                        "input": {"path": "PROMPT.md"},
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu_stat",
                        "name": "mcp__ralph__stat_path",
                        "input": {"path": "tmp"},
                    },
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_read",
                        "content": [{"type": "text", "text": "prompt contents"}],
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_stat",
                        "content": [{"type": "text", "text": "directory metadata"}],
                    }
                ]
            },
        },
    ]
    for event in events:
        bridged_lines.extend(
            transcript_lines_from_event(json.dumps(event), parser=transcript_parser)
        )

    parsed = list(ClaudeInteractiveParser().parse(iter(bridged_lines)))
    tool_uses = [line for line in parsed if line.type == "tool_use"]
    tool_results = [line for line in parsed if line.type == "tool_result"]

    assert tool_uses[0].metadata["input"] == {"path": "PROMPT.md"}
    assert [line.metadata["tool"] for line in tool_results] == [
        "mcp__ralph__read_file",
        "mcp__ralph__stat_path",
    ]
    assert "unknown" not in {str(line.metadata["tool"]) for line in tool_results}
