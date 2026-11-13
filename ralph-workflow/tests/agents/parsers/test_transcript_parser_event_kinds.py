from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers.claude_interactive_transcript_parser import (
    ClaudeInteractiveTranscriptParser,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


def _line(obj: dict[str, object]) -> str:
    return json.dumps(obj)


class TestTranscriptParserEventKinds:
    def test_transcript_parser_kinds_cover_typed_frames(self) -> None:
        parser = ClaudeInteractiveTranscriptParser()
        events = parser.feed(
            _line(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "thinking", "thinking": "plan"},
                            {"type": "tool_use", "id": "t1", "name": "bash", "input": {}},
                        ]
                    },
                }
            )
        )
        assert [event.kind for event in events] == ["thinking", "tool_use"]
