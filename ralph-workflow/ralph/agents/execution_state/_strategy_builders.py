"""Built-in execution-strategy builders and activity classifiers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ralph.agents.activity import AgentActivityKind, AgentActivitySignal

from ._completion_mixin import CompletionEnforcingStrategy
from .generic_execution_strategy import GenericExecutionStrategy
from .opencode_execution_strategy import OpenCodeExecutionStrategy

if TYPE_CHECKING:
    from ralph.process.child_liveness import ChildLivenessRegistry
    from ralph.process.monitor import SubagentPidSource

    from ._base import BaseExecutionStrategy


def _make_opencode_strategy(
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
    subagent_pid_source: SubagentPidSource | None = None,
    **_kwargs: object,
) -> BaseExecutionStrategy:
    del _kwargs
    return OpenCodeExecutionStrategy(
        label_scope=label_scope, registry=registry, subagent_pid_source=subagent_pid_source
    )


def _make_agy_strategy(
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
    subagent_pid_source: SubagentPidSource | None = None,
    **_kwargs: object,
) -> BaseExecutionStrategy:
    del _kwargs

    class AgyExecutionStrategy(CompletionEnforcingStrategy, GenericExecutionStrategy):
        def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
            return _classify_agy_activity(line) or super().classify_activity_line(line)

        def supports_incomplete_exit_reprompt(self) -> bool:
            return True

    return AgyExecutionStrategy(
        label_scope=label_scope, registry=registry, subagent_pid_source=subagent_pid_source
    )


def _classify_agy_activity(line: str) -> AgentActivitySignal | None:
    obj = _parse_json_object(line)
    if obj is None or obj.get("event") != "step_update":
        return None
    raw_step = obj.get("step_update")
    if not isinstance(raw_step, dict):
        return None
    step_update = cast("dict[str, object]", raw_step)
    if step_update.get("step_type") not in {"tool", "subagent"}:
        return None
    if step_update.get("state") == "DONE":
        return AgentActivitySignal(AgentActivityKind.TOOL_RESULT, raw=line)
    return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line)


def _make_cursor_strategy(
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
    subagent_pid_source: SubagentPidSource | None = None,
    **_kwargs: object,
) -> BaseExecutionStrategy:
    del _kwargs

    class CursorExecutionStrategy(CompletionEnforcingStrategy, GenericExecutionStrategy):
        def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
            return _classify_cursor_activity(line) or super().classify_activity_line(line)

    return CursorExecutionStrategy(
        label_scope=label_scope, registry=registry, subagent_pid_source=subagent_pid_source
    )


def _classify_cursor_activity(line: str) -> AgentActivitySignal | None:
    obj = _parse_json_object(line)
    if obj is None:
        return None
    event_type = obj.get("type")
    if event_type == "tool_result":
        result = obj.get("result")
        return AgentActivitySignal(
            AgentActivityKind.TOOL_RESULT,
            raw=line,
            is_harness_echo=isinstance(result, str)
            and "Read the complete prompt from file at" in result,
        )
    if event_type != "tool_call":
        return None
    return AgentActivitySignal(
        AgentActivityKind.TOOL_RESULT
        if obj.get("subtype") == "completed"
        else AgentActivityKind.TOOL_USE,
        raw=line,
    )


def _make_kimi_strategy(
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
    subagent_pid_source: SubagentPidSource | None = None,
    **_kwargs: object,
) -> BaseExecutionStrategy:
    del _kwargs

    class KimiExecutionStrategy(CompletionEnforcingStrategy, GenericExecutionStrategy):
        def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
            return _classify_kimi_activity(line) or super().classify_activity_line(line)

    return KimiExecutionStrategy(
        label_scope=label_scope, registry=registry, subagent_pid_source=subagent_pid_source
    )


def _classify_kimi_activity(line: str) -> AgentActivitySignal | None:
    obj = _parse_json_object(line)
    if obj is None:
        return None
    if obj.get("role") == "tool":
        return AgentActivitySignal(AgentActivityKind.TOOL_RESULT, raw=line)
    if (
        obj.get("role") == "assistant"
        and isinstance(obj.get("tool_calls"), list)
        and obj["tool_calls"]
    ):
        return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line)
    return None


def _make_pi_strategy(
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
    subagent_pid_source: SubagentPidSource | None = None,
    **_kwargs: object,
) -> BaseExecutionStrategy:
    del _kwargs

    class PiExecutionStrategy(CompletionEnforcingStrategy, GenericExecutionStrategy):
        def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
            return _classify_pi_activity(line) or super().classify_activity_line(line)

        def supports_session_continuation(self) -> bool:
            return True

    return PiExecutionStrategy(
        label_scope=label_scope, registry=registry, subagent_pid_source=subagent_pid_source
    )


def _classify_pi_activity(line: str) -> AgentActivitySignal | None:
    obj = _parse_json_object(line)
    if obj is None:
        return None
    return _classify_pi_tool_event(obj, line) or _classify_pi_message_event(obj, line)


def _classify_pi_tool_event(obj: dict[str, object], line: str) -> AgentActivitySignal | None:
    if obj.get("type") == "tool_execution_start":
        return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line)
    if obj.get("type") == "tool_execution_end" and obj.get("isError") is True:
        return AgentActivitySignal(
            AgentActivityKind.ERROR_LINE,
            raw=_pi_result_text(obj.get("result")) or "tool execution failed",
        )
    return None


def _classify_pi_message_event(obj: dict[str, object], line: str) -> AgentActivitySignal | None:
    if obj.get("type") != "message_update":
        return None
    assistant_event = obj.get("assistantMessageEvent")
    if not isinstance(assistant_event, dict):
        return None
    event = cast("dict[str, object]", assistant_event)
    if event.get("type") == "toolcall_end":
        return AgentActivitySignal(AgentActivityKind.TOOL_RESULT, raw=line)
    if event.get("type") == "error":
        return AgentActivitySignal(
            AgentActivityKind.ERROR_LINE, raw=str(event.get("reason", "error"))
        )
    return None


def _parse_json_object(line: str) -> dict[str, object] | None:
    if not line.strip():
        return None
    try:
        parsed: object = json.loads(line.strip(), strict=False)
    except (json.JSONDecodeError, ValueError):
        return None
    return cast("dict[str, object]", parsed) if isinstance(parsed, dict) else None


def _pi_result_text(result: object) -> str:
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return str(result)
    content_value: object = result.get("content")
    if not isinstance(content_value, list):
        return str(result)
    content: list[object] = content_value
    parts: list[str] = []
    for raw_block in content:
        block: object = raw_block
        if not isinstance(block, dict) or str(block.get("type")) != "text":
            continue
        text: object = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)
