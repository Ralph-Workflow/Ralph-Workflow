"""Execution strategy for Claude agents."""

from __future__ import annotations

import json
from typing import cast

from ralph.agents.activity import AgentActivityKind, AgentActivitySignal

from ._helpers import _classify_claude_json_object, _classify_claude_prefixed_line
from .generic_execution_strategy import GenericExecutionStrategy


class ClaudeExecutionStrategy(GenericExecutionStrategy):
    """Claude-aware activity classification for watchdog control flow."""

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        stripped = line.strip()
        if not stripped:
            return None

        prefixed_signal = _classify_claude_prefixed_line(stripped)
        if prefixed_signal is not None:
            return prefixed_signal

        try:
            parsed = cast("object", json.loads(stripped, strict=False))
        except json.JSONDecodeError:
            return AgentActivitySignal(AgentActivityKind.OUTPUT_LINE, raw=line)

        if not isinstance(parsed, dict):
            return AgentActivitySignal(AgentActivityKind.OUTPUT_LINE, raw=line)

        obj = cast("dict[str, object]", parsed)
        return _classify_claude_json_object(obj, line)
