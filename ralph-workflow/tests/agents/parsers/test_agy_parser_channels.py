from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers.agy import AgyParser
from ralph.agents.parsers.content_channel import ContentChannel

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


def _line(obj: dict[str, object]) -> str:
    return json.dumps(obj)


class TestAgyParserChannels:
    def test_tool_step_done_is_tool_call_then_tool_result(self) -> None:
        raw = _line(
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "tool",
                    "step_index": 2,
                    "state": "DONE",
                    "tool_info": {"name": "Read", "parameters": {"file_path": "/x"}},
                    "duration_seconds": 1.0,
                },
            }
        )
        results = list(AgyParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("tool_use", ContentChannel.TOOL_CALL),
            ("tool_result", ContentChannel.TOOL_RESULT),
        ]

    def test_subagent_step_frames_are_subagent(self) -> None:
        raw = _line(
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "subagent",
                    "step_index": 1,
                    "state": "DONE",
                    "subagent_info": {"subagents": [{"role": "worker", "type_name": "explore"}]},
                },
            }
        )
        results = list(AgyParser().parse(_lines(raw)))
        assert [line.channel for line in results] == [
            ContentChannel.SUBAGENT,
            ContentChannel.SUBAGENT,
        ]

    def test_error_event_is_provider(self) -> None:
        raw = _line({"event": "error", "message": "RESOURCE_EXHAUSTED (code 429)"})
        results = list(AgyParser().parse(_lines(raw)))
        assert [(line.type, line.channel) for line in results] == [
            ("error", ContentChannel.PROVIDER)
        ]
