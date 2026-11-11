
from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from ._ndjson_base import NdjsonParserBase
from .agent_output_line import AgentOutputLine
from .text_accumulator import TextAccumulator

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry


_TEXT_KIND = "text"
_THINKING_KIND = "thinking"

_TEXT_ACCUMULATOR_PREFIX = "text:"
_THINKING_ACCUMULATOR_PREFIX = "thinking:"

_LEGACY_UNINDEXED_CONTENT_INDEX = -1


def _content_index_of(sub: dict[str, object]) -> int:
    raw = sub.get("contentIndex")
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    return _LEGACY_UNINDEXED_CONTENT_INDEX


def _accumulator_key(kind: str, content_index: int) -> str:
    return f"{kind}:{content_index}"


def _extract_tool_result_text(result: object) -> str:
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return str(result) if result else ""
    content = result.get("content")
    if not isinstance(content, list):
        return str(result)
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            block_dict = cast("dict[str, object]", block)
            if block_dict.get("type") == "text":
                text = block_dict.get("text", "")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


_PI_PASSTHROUGH_TOP_LEVEL_EVENTS: frozenset[str] = frozenset(
    {
        "queue_update",
        "compaction_start",
        "compaction_end",
    }
)


_PI_ERROR_STOP_REASON = "error"


def _message_failure_text(message: object) -> str:
    if not isinstance(message, dict):
        return ""
    message_dict = cast("dict[str, object]", message)
    stop_reason = message_dict.get("stopReason")
    if not isinstance(stop_reason, str):
        return ""
    if stop_reason.casefold() != _PI_ERROR_STOP_REASON:
        return ""
    error_message = message_dict.get("errorMessage")
    if isinstance(error_message, str) and error_message.strip():
        return error_message
    return "model turn failed (stopReason=error)"


_PI_STOP_EVENTS: frozenset[str] = frozenset({"agent_end", "turn_end"})


_PI_SILENT_TOP_LEVEL_EVENTS: frozenset[str] = frozenset(
    {"agent_start", "turn_start", "message_start"}
)


_PI_SILENT_SUB_EVENTS: frozenset[str] = frozenset({"text_start", "thinking_start"})


def _make_passthrough(
    event_type: str,
) -> Callable[[dict[str, object], str], Iterator[AgentOutputLine]]:
    def _handler(obj: dict[str, object], stripped: str) -> Iterator[AgentOutputLine]:
        yield AgentOutputLine(type=event_type, raw=stripped, metadata=obj)

    return _handler


class _PiDispatch:

    def __init__(self, owner: PiParser) -> None:
        self._owner = owner
        self._top_level_handlers: dict[
            str, Callable[[dict[str, object], str], Iterator[AgentOutputLine]]
        ] = {
            "session": self._handle_session,
            "message_end": self._handle_message_end,
            "message_update": self._handle_message_update,
            "tool_execution_start": self._handle_tool_execution_start,
            "tool_execution_update": self._handle_tool_execution_update,
            "tool_execution_end": self._handle_tool_execution_end,
            "extension_error": self._handle_extension_error,
            "auto_retry_start": self._handle_auto_retry_start,
            "auto_retry_end": self._handle_auto_retry_end,
        }
        for _evt in _PI_PASSTHROUGH_TOP_LEVEL_EVENTS:
            self._top_level_handlers[_evt] = _make_passthrough(_evt)
        self._sub_event_handlers: dict[
            str, Callable[[dict[str, object], str], Iterator[AgentOutputLine]]
        ] = {
            "text_delta": self._handle_text_delta,
            "text_end": self._handle_text_end,
            "thinking_delta": self._handle_thinking_delta,
            "thinking_end": self._handle_thinking_end,
            "toolcall_start": self._handle_toolcall_start,
            "toolcall_delta": self._handle_toolcall_delta,
            "toolcall_end": self._handle_toolcall_end,
            "done": self._handle_done,
            "error": self._handle_message_error,
        }

    def dispatch(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        event_type = str(obj.get("type", "unknown"))

        if event_type in _PI_SILENT_TOP_LEVEL_EVENTS:
            return

        if event_type in _PI_STOP_EVENTS:
            yield from self._owner.flush_accumulators()
            self._owner.reset_emission_flags()
            yield AgentOutputLine(type="stop", raw=stripped, metadata=obj)
            return

        handler = self._top_level_handlers.get(event_type)
        if handler is not None:
            yield from handler(obj, stripped)
            return

        yield AgentOutputLine(type=event_type, raw=stripped, metadata=obj)

    def _handle_session(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        metadata: dict[str, object] = dict(obj)
        yield AgentOutputLine(type="session", raw=stripped, metadata=metadata)

    def _handle_message_end(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        for key in list(self._owner._accumulators.keys()):
            self._owner._accumulators.pop(key, None)
        yield from self._emit_message_content(obj, stripped)
        failure_text = _message_failure_text(obj.get("message"))
        if failure_text:
            yield AgentOutputLine(
                type="error",
                content=failure_text,
                raw=stripped,
                metadata=obj,
            )
        self._owner.reset_emission_flags()

    def _handle_auto_retry_start(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        attempt = obj.get("attempt")
        max_attempts = obj.get("maxAttempts")
        error_message = obj.get("errorMessage")
        parts: list[str] = []
        if isinstance(attempt, int) and isinstance(max_attempts, int):
            parts.append(f"retry {attempt}/{max_attempts}")
        elif isinstance(attempt, int):
            parts.append(f"retry {attempt}")
        else:
            parts.append("retry")
        delay_ms = obj.get("delayMs")
        if isinstance(delay_ms, int) and not isinstance(delay_ms, bool):
            parts.append(f"after {delay_ms}ms")
        if isinstance(error_message, str) and error_message.strip():
            parts.append(f"- {error_message}")
        yield AgentOutputLine(
            type="auto_retry_start",
            content=" ".join(parts),
            raw=stripped,
            metadata=obj,
        )

    def _handle_auto_retry_end(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        success = obj.get("success")
        if success is False:
            final_error = obj.get("finalError")
            content = (
                final_error
                if isinstance(final_error, str) and final_error.strip()
                else "agent retries exhausted"
            )
            yield AgentOutputLine(
                type="error",
                content=content,
                raw=stripped,
                metadata=obj,
            )
            return
        yield AgentOutputLine(type="auto_retry_end", raw=stripped, metadata=obj)

    def _handle_message_update(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        assistant_event = obj.get("assistantMessageEvent")
        if not isinstance(assistant_event, dict):
            return
        sub = cast("dict[str, object]", assistant_event)
        sub_type = str(sub.get("type", "unknown"))
        if sub_type in _PI_SILENT_SUB_EVENTS:
            return
        handler = self._sub_event_handlers.get(sub_type)
        if handler is not None:
            yield from handler(sub, stripped)
            return
        yield AgentOutputLine(type=sub_type, raw=stripped, metadata=sub)

    def _handle_tool_execution_start(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        tool_name = str(obj.get("toolName", "unknown"))
        args = obj.get("args", {})
        yield AgentOutputLine(
            type="tool_use",
            content=tool_name,
            raw=stripped,
            metadata={"tool": tool_name, "args": args, "tool_call_id": obj.get("toolCallId")},
        )

    def _handle_tool_execution_update(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        del obj, stripped
        return iter(())

    def _handle_tool_execution_end(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        is_error = obj.get("isError", False)
        result = obj.get("result", "")
        extracted = _extract_tool_result_text(result)
        if is_error:
            yield AgentOutputLine(
                type="error",
                content=extracted if extracted else "tool execution failed",
                raw=stripped,
                metadata={
                    **obj,
                    "tool": str(obj.get("toolName", "unknown")),
                    "tool_call_id": obj.get("toolCallId"),
                },
            )
            return
        yield AgentOutputLine(
            type="tool_result",
            content=extracted,
            raw=stripped,
            metadata={
                **obj,
                "tool": str(obj.get("toolName", "unknown")),
                "tool_call_id": obj.get("toolCallId"),
            },
        )

    def _handle_extension_error(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        error_msg = str(obj.get("error", "extension error"))
        yield AgentOutputLine(type="error", content=error_msg, raw=stripped, metadata=obj)

    def _handle_text_delta(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        delta = str(sub.get("delta", ""))
        if not delta:
            return
        content_index = _content_index_of(sub)
        if content_index in self._owner.saw_text_end_by_index:
            return
        acc = self._get_text_accumulator(content_index)
        yield from acc.accumulate(delta, stripped, kind=_TEXT_KIND, keep_current_when_empty=True)

    def _handle_text_end(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        content = str(sub.get("content", ""))
        content_index = _content_index_of(sub)
        self._owner.saw_text_end_by_index.add(content_index)
        acc_key = _accumulator_key(_TEXT_KIND, content_index)
        if content:
            self._owner._accumulators.pop(acc_key, None)
            yield AgentOutputLine(type=_TEXT_KIND, content=content, raw=stripped, metadata=sub)
            return
        acc = self._owner._accumulators.pop(acc_key, None)
        if acc is not None:
            yield from acc.flush(kind=_TEXT_KIND)

    def _handle_thinking_delta(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        delta = str(sub.get("delta", ""))
        if not delta:
            return
        content_index = _content_index_of(sub)
        if content_index in self._owner.saw_thinking_end_by_index:
            return
        acc = self._get_thinking_accumulator(content_index)
        yield from acc.accumulate(
            delta,
            stripped,
            kind=_THINKING_KIND,
            keep_current_when_empty=True,
        )

    def _handle_thinking_end(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        content = str(sub.get("content", ""))
        content_index = _content_index_of(sub)
        self._owner.saw_thinking_end_by_index.add(content_index)
        acc_key = _accumulator_key(_THINKING_KIND, content_index)
        if content.strip():
            self._owner._accumulators.pop(acc_key, None)
            yield AgentOutputLine(
                type=_THINKING_KIND,
                content=content,
                raw=stripped,
                metadata=sub,
            )
            return
        acc = self._owner._accumulators.pop(acc_key, None)
        if acc is not None:
            yield from acc.flush(kind=_THINKING_KIND, require_strip=True)

    def _handle_toolcall_start(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        return iter([])

    def _handle_toolcall_delta(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        return iter([])

    def _handle_toolcall_end(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        tool_call = sub.get("toolCall")
        tool_name = "unknown"
        args: object = {}
        if isinstance(tool_call, dict):
            tool_call_dict = cast("dict[str, object]", tool_call)
            tool_name = str(tool_call_dict.get("name", "unknown"))
            args = tool_call_dict.get("arguments", tool_call_dict.get("input", {}))
        yield AgentOutputLine(
            type="tool_use",
            content=tool_name,
            raw=stripped,
            metadata={"tool": tool_name, "args": args, **sub},
        )

    def _handle_done(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        yield from self._owner.flush_accumulators()
        self._owner.reset_emission_flags()
        stop_reason = sub.get("stopReason")
        if isinstance(stop_reason, str) and stop_reason.casefold() == "length":
            yield AgentOutputLine(
                type="error",
                content="context length exhausted",
                raw=stripped,
                metadata=sub,
            )
        yield AgentOutputLine(type="stop", raw=stripped, metadata=sub)

    def _handle_message_error(
        self,
        sub: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        reason = str(sub.get("reason", "error"))
        yield AgentOutputLine(type="error", content=reason, raw=stripped, metadata=sub)

    def _emit_message_content(
        self,
        obj: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        message = obj.get("message")
        if not isinstance(message, dict):
            return
        message_dict = cast("dict[str, object]", message)
        content = message_dict.get("content")
        if not isinstance(content, list):
            return
        content_list = cast("list[object]", content)
        for block_index, block in enumerate(content_list):
            if not isinstance(block, dict):
                continue
            block_dict = cast("dict[str, object]", block)
            block_type = str(block_dict.get("type", ""))
            if block_type == _TEXT_KIND and block_index not in self._owner.saw_text_end_by_index:
                acc_key = _accumulator_key(_TEXT_KIND, block_index)
                self._owner._accumulators.pop(acc_key, None)
                self._owner.saw_text_end_by_index.add(block_index)
                yield from self._handle_text_block(block_dict, stripped)
            elif (
                block_type == _THINKING_KIND
                and block_index not in self._owner.saw_thinking_end_by_index
            ):
                acc_key = _accumulator_key(_THINKING_KIND, block_index)
                self._owner._accumulators.pop(acc_key, None)
                self._owner.saw_thinking_end_by_index.add(block_index)
                yield from self._handle_thinking_block(block_dict, stripped)
            elif block_type == "toolCall":
                yield from self._handle_toolcall_block(block_dict, stripped)
            elif block_type == "toolResult":
                yield from self._handle_toolresult_block(block_dict, stripped)

    def _handle_text_block(
        self,
        block_dict: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        text = str(block_dict.get("text", ""))
        if text:
            yield AgentOutputLine(type="text", content=text, raw=stripped, metadata=block_dict)

    def _handle_thinking_block(
        self,
        block_dict: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        thinking = str(block_dict.get("thinking", ""))
        if thinking:
            yield AgentOutputLine(
                type="thinking",
                content=thinking,
                raw=stripped,
                metadata=block_dict,
            )

    def _handle_toolcall_block(
        self,
        block_dict: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        tool_name = str(block_dict.get("name", "unknown"))
        yield AgentOutputLine(
            type="tool_use",
            content=tool_name,
            raw=stripped,
            metadata=block_dict,
        )

    def _handle_toolresult_block(
        self,
        block_dict: dict[str, object],
        stripped: str,
    ) -> Iterator[AgentOutputLine]:
        is_error = block_dict.get("isError", False)
        result = block_dict.get("result", "")
        extracted = _extract_tool_result_text(result)
        if is_error:
            yield AgentOutputLine(
                type="error",
                content=extracted if extracted else "tool result error",
                raw=stripped,
                metadata=block_dict,
            )
            return
        yield AgentOutputLine(
            type="tool_result",
            content=extracted,
            raw=stripped,
            metadata=block_dict,
        )

    def _get_text_accumulator(self, content_index: int) -> TextAccumulator:
        accumulators = self._owner._accumulators
        key = _accumulator_key(_TEXT_KIND, content_index)
        if key not in accumulators:
            accumulators[key] = TextAccumulator()
        return accumulators[key]

    def _get_thinking_accumulator(self, content_index: int) -> TextAccumulator:
        accumulators = self._owner._accumulators
        key = _accumulator_key(_THINKING_KIND, content_index)
        if key not in accumulators:
            accumulators[key] = TextAccumulator()
        return accumulators[key]


class PiParser(NdjsonParserBase):

    _STOP_EVENT_TYPES: ClassVar[frozenset[str]] = frozenset()

    def __init__(
        self,
        subagent_pid_registry: SubagentPidRegistry | None = None,
        subagent_source_label: str | None = None,
    ) -> None:
        super().__init__()
        self._subagent_pid_registry: SubagentPidRegistry | None = subagent_pid_registry
        self._subagent_source_label: str | None = subagent_source_label
        self._accumulators: dict[str, TextAccumulator] = {}
        self.saw_text_end_by_index: set[int] = set()
        self.saw_thinking_end_by_index: set[int] = set()
        self._dispatcher = _PiDispatch(self)

    def reset_emission_flags(self) -> None:
        self.saw_text_end_by_index = set()
        self.saw_thinking_end_by_index = set()

    def _handle_lifecycle_event(
        self,
        obj: dict[str, object],
        event_type: str,
    ) -> Iterator[AgentOutputLine] | None:
        return None

    def _dispatch_json_object(
        self,
        obj: dict[str, object],
        raw: str,
        source_timestamp: str | None = None,
    ) -> Iterator[AgentOutputLine]:
        del source_timestamp
        self._try_register_subagent_pid_from_obj(obj)
        yield from self._dispatcher.dispatch(obj, raw)

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        keys = list(self._accumulators.keys())
        for key in keys:
            acc = self._accumulators.pop(key, None)
            if acc is None:
                continue
            if key.startswith(_THINKING_ACCUMULATOR_PREFIX):
                yield from acc.flush(kind=_THINKING_KIND, require_strip=True)
            elif key.startswith(_TEXT_ACCUMULATOR_PREFIX):
                yield from acc.flush(kind=_TEXT_KIND)
