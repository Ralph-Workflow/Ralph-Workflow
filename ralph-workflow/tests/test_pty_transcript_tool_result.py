from __future__ import annotations

import json

from ralph.agents.invoke._pty_transcript import transcript_lines_from_event


def test_transcript_lines_from_assistant_tool_result_event_emits_result_line() -> None:
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

    assert lines == ["claude result: Exit code: 0\nStdout:\nhello\n"]


def test_transcript_lines_from_assistant_thinking_event_emits_thinking_line() -> None:
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

    assert lines == ["Let me compare these plan steps before editing.\n"]
