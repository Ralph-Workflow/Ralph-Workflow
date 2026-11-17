from __future__ import annotations

import contextlib
import json
import re
from typing import TYPE_CHECKING

from ralph.agents.activity import AgentActivityKind, AgentActivitySignal
from ralph.agents.completion_signals import completion_signals_terminal
from ralph.agents.execution_state._harness_echo import is_prompt_echo_line
from ralph.agents.parsers.claude_interactive import ClaudeInteractiveTranscriptParser
from ralph.display.vt_normalizer import normalize_vt_text
from ralph.mcp.server._activity_sink import (
    get_subagent_sink as _has_subagent_sink,
)
from ralph.mcp.server._activity_sink import (
    invoke_subagent_sink as _invoke_subagent_sink,
)
from ralph.process.child_liveness import classify_child_snapshot

from ._helpers import (
    _AGENT_LABEL_PREFIX,
    _classify_claude_json_object,
    _classify_claude_prefixed_line,
    _classify_generic_child_signal,
    _classify_opencode_child_signal,
    _error_message_from_error_field,
    _error_output_signal,
    _evidence_precedence,
    _non_blank_output_signal,
    _opencode_step_frame_signal,
    _os_descendant_state,
    _probe_check_quiet,
    _progress_report_signal,
    _route_opencode_line_to_registry,
    as_object_dict,
    loads_json_object,
    parse_opencode_child_id,
)
from .agent_execution_state import AgentExecutionState

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ralph.agents.completion_signals import CompletionSignals
    from ralph.agents.parsers.interactive_transcript_event import InteractiveTranscriptEvent
    from ralph.process.child_liveness import ChildLivenessRegistry
    from ralph.process.liveness import LivenessProbe
    from ralph.process.monitor import SubagentPidSource

    from ._live_descendant_handle import LiveDescendantHandle


def with_prompt_echo_flag(
    activity_signal: AgentActivitySignal,
    queued_line: str,
    input_prompt: str | None,
) -> AgentActivitySignal:
    if not is_prompt_echo_line(queued_line, input_prompt):
        return activity_signal
    return AgentActivitySignal(
        kind=activity_signal.kind,
        raw=activity_signal.raw,
        error_message=activity_signal.error_message,
        is_harness_echo=True,
    )


class BaseExecutionStrategy:
    __slots__ = ("_label_scope", "_registry", "_subagent_pid_source")

    def __init__(
        self,
        *,
        label_scope: str | None = None,
        registry: ChildLivenessRegistry | None = None,
        subagent_pid_source: SubagentPidSource | None = None,
    ) -> None:
        self._label_scope = label_scope
        self._registry = registry
        self._subagent_pid_source = subagent_pid_source

    def observe_line(self, line: str) -> None:
        if not _has_subagent_sink():
            return
        signal = _classify_generic_child_signal(line)
        if signal is None:
            return
        if signal.kind in (
            AgentActivityKind.CHILD_PROGRESS,
            AgentActivityKind.CHILD_HEARTBEAT,
        ):
            with contextlib.suppress(Exception):
                _invoke_subagent_sink(line)

    def observe_stream_end(self) -> None:
        return

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        progress_signal = _progress_report_signal(line)
        if progress_signal is not None:
            return progress_signal
        error_signal = _error_output_signal(line)
        if error_signal is not None:
            return error_signal
        return _non_blank_output_signal(line)

    def classify_quiet(
        self,
        handle: LiveDescendantHandle,
        liveness_probe: LivenessProbe,
    ) -> AgentExecutionState:
        del liveness_probe
        if self._registry is not None:
            with contextlib.suppress(OSError, RuntimeError, ValueError, AttributeError):
                reg_snapshot = self._registry.snapshot("")
                if reg_snapshot.active_count > 0:
                    return AgentExecutionState.WAITING_ON_CHILD
            return AgentExecutionState.ACTIVE
        if self._subagent_pid_source is not None:
            try:
                pids = self._subagent_pid_source.known_subagent_pids()
            except (OSError, RuntimeError, ValueError, AttributeError):
                pids = set()
            if pids:
                return AgentExecutionState.WAITING_ON_CHILD
            return AgentExecutionState.ACTIVE
        if hasattr(handle, "has_live_descendants"):
            with contextlib.suppress(
                OSError, RuntimeError, ValueError, AttributeError, StopIteration
            ):
                if bool(handle.has_live_descendants()):
                    return AgentExecutionState.WAITING_ON_CHILD
        return AgentExecutionState.ACTIVE

    def classify_exit(
        self,
        handle: LiveDescendantHandle,
        completion_signals: CompletionSignals,
        liveness_probe: LivenessProbe | None = None,
    ) -> AgentExecutionState:
        del handle, completion_signals, liveness_probe
        return AgentExecutionState.TERMINAL_COMPLETE

    def supports_session_continuation(self) -> bool:
        return False

    def supports_completion_enforcement(self) -> bool:
        return False

    def supports_incomplete_exit_reprompt(self) -> bool:
        return False


class CompletionEnforcingStrategy:
    __slots__ = ()

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        own_namespace: Mapping[str, object] = cls.__dict__
        if "supports_completion_enforcement" in own_namespace:
            msg = (
                f"{cls.__name__} must not override supports_completion_enforcement(); "
                "CompletionEnforcingStrategy is the sole source of truth"
            )
            raise TypeError(msg)

    def supports_completion_enforcement(self) -> bool:
        return True

    def classify_exit(
        self,
        handle: LiveDescendantHandle,
        completion_signals: CompletionSignals,
        liveness_probe: LivenessProbe | None = None,
    ) -> AgentExecutionState:
        del handle, liveness_probe
        if completion_signals_terminal(completion_signals):
            return AgentExecutionState.TERMINAL_COMPLETE
        return AgentExecutionState.RESUMABLE_CONTINUE


_MULTIPLICATION_SIGN = chr(215)
_NANOCODER_EXECUTED_TOOL_RE = re.compile(
    rf"^⚒\s+Executed\s+(?P<tool>\S+)(?:\s+{_MULTIPLICATION_SIGN}\s+\d+)?$"
)
_PLAIN_TOOL_PREFIX = "[plain] tool:"

_RAW_TOOL_USE_TYPES = frozenset(
    {
        "tool_use",
        "tool_call",
        "function_call",
        "mcp_tool_call",
        "tool_execution_start",
    }
)
_RAW_TOOL_RESULT_TYPES = frozenset(
    {
        "tool_result",
        "mcp_tool_result",
        "tool_execution_end",
    }
)
_ITEM_TOOL_TYPES = frozenset(
    {
        "mcp_tool_call",
        "command_execution",
        "tool_use",
        "tool_call",
        "function_call",
    }
)


def _classify_item_event(
    obj: dict[str, object],
    line: str,
) -> AgentActivitySignal | None:
    item = obj.get("item")
    if not isinstance(item, dict):
        return None
    item_dict = as_object_dict(item) or {}
    item_type = str(item_dict.get("type", ""))
    if item_type not in _ITEM_TOOL_TYPES:
        return None
    error_msg = str(item_dict.get("error")) if item_dict.get("error") else None
    event_type = str(obj.get("type", obj.get("event", "")))
    if event_type == "item.completed":
        return AgentActivitySignal(AgentActivityKind.TOOL_RESULT, raw=line, error_message=error_msg)
    return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line, error_message=error_msg)


def _classify_gemini_event(
    obj: dict[str, object],
    line: str,
) -> AgentActivitySignal | None:
    candidates = obj.get("candidates")
    if isinstance(candidates, list) and candidates:
        first = candidates[0]
        if isinstance(first, dict):
            content = (as_object_dict(first) or {}).get("content")
            if isinstance(content, dict):
                cand_parts = (as_object_dict(content) or {}).get("parts")
                if isinstance(cand_parts, list) and any(
                    isinstance(p, dict) and "function_call" in p for p in cand_parts
                ):
                    return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line)

    parts = obj.get("parts")
    if isinstance(parts, list) and any(isinstance(p, dict) and "function_call" in p for p in parts):
        return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line)

    if "function_call" in obj:
        return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line)

    return None


def _classify_generic_event_type(
    obj: dict[str, object],
    line: str,
) -> AgentActivitySignal | None:
    event_type = str(obj.get("type", obj.get("event", "")))

    if obj.get("subtype") == "completed" and event_type in _RAW_TOOL_USE_TYPES:
        return AgentActivitySignal(AgentActivityKind.TOOL_RESULT, raw=line)

    if event_type in _RAW_TOOL_USE_TYPES:
        return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line)

    if event_type in _RAW_TOOL_RESULT_TYPES:
        return AgentActivitySignal(AgentActivityKind.TOOL_RESULT, raw=line)

    if "tool_name" in obj or "toolName" in obj or "toolCall" in obj:
        return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line)

    return None


def _classify_generic_tool_frame(
    obj: dict[str, object],
    line: str,
) -> AgentActivitySignal | None:
    return (
        _classify_item_event(obj, line)
        or _classify_gemini_event(obj, line)
        or _classify_generic_event_type(obj, line)
    )


class GenericExecutionStrategy(BaseExecutionStrategy):
    __slots__ = ()

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        stripped = line.strip()
        if not stripped:
            return None

        normalized = normalize_vt_text(stripped).strip()
        if _NANOCODER_EXECUTED_TOOL_RE.match(normalized) or normalized.startswith(
            _PLAIN_TOOL_PREFIX
        ):
            return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line)

        progress_signal = _progress_report_signal(line)
        if progress_signal is not None:
            return progress_signal

        payload = stripped[5:].strip() if stripped.startswith("data:") else stripped
        if payload.startswith("{"):
            try:
                parsed: object = json.loads(payload, strict=False)
            except (json.JSONDecodeError, ValueError):
                parsed = None

            obj = as_object_dict(parsed) if parsed is not None else None
            if obj is not None:
                if obj.get("type") == "error":
                    return AgentActivitySignal(
                        AgentActivityKind.ERROR_LINE, raw=_error_message_from_error_field(obj)
                    )
                tool_signal = _classify_generic_tool_frame(obj, line)
                if tool_signal is not None:
                    return tool_signal

        return super().classify_activity_line(line)


def _step_frame_message_id(envelope: dict[str, object]) -> str | None:
    part = envelope.get("part")
    if not isinstance(part, dict):
        return None
    message_id = (as_object_dict(part) or {}).get("messageID")
    if isinstance(message_id, str) and message_id:
        return message_id
    return None


class OpenCodeExecutionStrategy(BaseExecutionStrategy):
    __slots__ = (
        "_anonymous_open_steps",
        "_open_step_messages",
        "_scoped_records_seen",
        "_subagent_activity_sink",
    )

    def __init__(
        self,
        *,
        label_scope: str | None = None,
        registry: ChildLivenessRegistry | None = None,
        subagent_pid_source: SubagentPidSource | None = None,
        subagent_activity_sink: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(
            label_scope=label_scope,
            registry=registry,
            subagent_pid_source=subagent_pid_source,
        )
        self._subagent_activity_sink = subagent_activity_sink
        self._scoped_records_seen = False
        self._open_step_messages: set[str] = set()
        self._anonymous_open_steps = 0

    def _active_label_prefix(self) -> str | None:
        if self._label_scope is None:
            return None
        return f"{_AGENT_LABEL_PREFIX}{self._label_scope}:"

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        signal = _classify_opencode_child_signal(line)
        if signal is not None:
            return signal
        error_signal = _error_output_signal(line)
        if error_signal is not None:
            return error_signal
        frame_signal = _opencode_step_frame_signal(line)
        if frame_signal is not None:
            return frame_signal
        progress_signal = _progress_report_signal(line)
        if progress_signal is not None:
            return progress_signal
        return _non_blank_output_signal(line)

    def observe_line(self, line: str) -> None:
        self._update_open_step_liveness(line)
        registry = self._registry
        child_id: str | None = parse_opencode_child_id(line)
        if self._subagent_activity_sink is not None or _has_subagent_sink():
            signal = _classify_opencode_child_signal(line)
            if signal is None:
                signal = _classify_generic_child_signal(line)
            if signal is not None and signal.kind in (
                AgentActivityKind.CHILD_PROGRESS,
                AgentActivityKind.CHILD_HEARTBEAT,
            ):
                if _classify_opencode_child_signal(line) is not None:
                    if child_id is not None:
                        if self._subagent_activity_sink is not None:
                            with contextlib.suppress(Exception):
                                self._subagent_activity_sink(line)
                        else:
                            _invoke_subagent_sink(line)
                elif self._subagent_activity_sink is not None:
                    with contextlib.suppress(Exception):
                        self._subagent_activity_sink(line)
                else:
                    _invoke_subagent_sink(line)
        if registry is None:
            return
        prefix = self._active_label_prefix() or ""
        _route_opencode_line_to_registry(line, registry, prefix)
        if registry.has_records(prefix):
            self._scoped_records_seen = True

    def record_native_child_progress(self, child_id: str) -> None:
        registry = self._registry
        if registry is None:
            return
        prefix = self._active_label_prefix() or ""
        if not registry.has_child(child_id):
            registry.register_child(child_id, prefix, phase="running")
        registry.record_progress(child_id, phase="running")
        self._scoped_records_seen = True

    @property
    def open_step_count(self) -> int:
        return len(self._open_step_messages) + self._anonymous_open_steps

    def observe_stream_end(self) -> None:
        self._open_step_messages.clear()
        self._anonymous_open_steps = 0

    def _update_open_step_liveness(self, line: str) -> None:
        try:
            decoded: object = json.loads(line)
        except json.JSONDecodeError:
            return
        envelope = as_object_dict(decoded)
        if envelope is None:
            return
        event_type = envelope.get("type")
        if event_type not in {"step_start", "step_finish"}:
            return
        message_id = _step_frame_message_id(envelope)
        if event_type == "step_start":
            if message_id is None:
                self._anonymous_open_steps += 1
            else:
                self._open_step_messages.add(message_id)
        elif message_id is None:
            self._anonymous_open_steps = max(0, self._anonymous_open_steps - 1)
        else:
            self._open_step_messages.discard(message_id)

    def classify_quiet(
        self,
        handle: LiveDescendantHandle,
        liveness_probe: LivenessProbe,
    ) -> AgentExecutionState:
        if self.open_step_count > 0:
            return AgentExecutionState.WAITING_ON_CHILD
        prefix = self._active_label_prefix()
        probe_prefix = prefix if prefix is not None else ""

        scoped_child_evidence_stale = False

        registry = self._registry
        if registry is not None:
            had_scoped_records = registry.has_records(probe_prefix)
            if had_scoped_records:
                self._scoped_records_seen = True
            if registry.has_active_phase(probe_prefix, "running"):
                running_snapshot = registry.snapshot(probe_prefix)
                running_verdict = classify_child_snapshot(running_snapshot)
                if running_verdict.deferral_allowed:
                    return AgentExecutionState.WAITING_ON_CHILD
            with contextlib.suppress(OSError, RuntimeError, ValueError, AttributeError):
                reg_snap = registry.snapshot(probe_prefix)
                verdict = classify_child_snapshot(reg_snap)
                if verdict.all_children_terminal or (
                    not verdict.deferral_allowed and had_scoped_records
                ):
                    return AgentExecutionState.ACTIVE
                if verdict.deferral_allowed:
                    return AgentExecutionState.WAITING_ON_CHILD

        probe_state, probe_stale = _probe_check_quiet(liveness_probe, probe_prefix)
        if probe_state is not None:
            return probe_state
        scoped_child_evidence_stale = scoped_child_evidence_stale or probe_stale

        return (
            AgentExecutionState.ACTIVE
            if scoped_child_evidence_stale or self._scoped_records_seen
            else _os_descendant_state(handle, AgentExecutionState.ACTIVE)
        )

    def classify_exit(
        self,
        handle: LiveDescendantHandle,
        completion_signals: CompletionSignals,
        liveness_probe: LivenessProbe | None = None,
    ) -> AgentExecutionState:
        registry = self._registry
        label_prefix = self._active_label_prefix()
        if self._scoped_records_seen:
            return _evidence_precedence(
                handle,
                completion_signals,
                liveness_probe,
                label_prefix,
                registry=registry,
                scoped_evidence_seen=True,
            )
        return _evidence_precedence(
            handle, completion_signals, liveness_probe, label_prefix, registry=registry
        )

    def supports_session_continuation(self) -> bool:
        return True

    def supports_completion_enforcement(self) -> bool:
        return False


class ClaudeExecutionStrategy(CompletionEnforcingStrategy, GenericExecutionStrategy):
    __slots__ = ()

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        stripped = line.strip()
        if not stripped:
            return None

        prefixed_signal = _classify_claude_prefixed_line(stripped)
        if prefixed_signal is not None:
            return prefixed_signal

        progress_signal = _progress_report_signal(line)
        if progress_signal is not None:
            return progress_signal

        try:
            parsed: object = json.loads(stripped, strict=False)
        except json.JSONDecodeError:
            return AgentActivitySignal(AgentActivityKind.OUTPUT_LINE, raw=line)

        obj = as_object_dict(parsed)
        if obj is None:
            return AgentActivitySignal(AgentActivityKind.OUTPUT_LINE, raw=line)
        return _classify_claude_json_object(obj, line)


def _interactive_tool_use_raw(event: InteractiveTranscriptEvent) -> str:
    metadata = event.metadata or {}
    tool_name = metadata.get("tool")
    if not isinstance(tool_name, str) or not tool_name:
        return event.text
    tool_input = metadata.get("input")
    payload: dict[str, object] = {
        "type": "tool_use",
        "name": tool_name,
        "input": tool_input if isinstance(tool_input, dict) else {},
    }
    return json.dumps(payload, sort_keys=True, default=str)


class ClaudeInteractiveExecutionStrategy(ClaudeExecutionStrategy):
    __slots__ = ("_transcript_parser",)

    def __init__(
        self,
        *,
        label_scope: str | None = None,
        registry: ChildLivenessRegistry | None = None,
        subagent_pid_source: SubagentPidSource | None = None,
    ) -> None:
        super().__init__(
            label_scope=label_scope,
            registry=registry,
            subagent_pid_source=subagent_pid_source,
        )
        self._transcript_parser = ClaudeInteractiveTranscriptParser()

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        events = self._transcript_parser.feed(line)
        if events:
            tool_result_event = None
            lifecycle_event = None
            thinking_event = None
            output_event = None
            for event in events:
                if event.kind == "tool_use":
                    return AgentActivitySignal(
                        AgentActivityKind.TOOL_USE,
                        raw=_interactive_tool_use_raw(event),
                    )
                if event.kind in {"lifecycle", "session"} and lifecycle_event is None:
                    lifecycle_event = event
                    continue
                if event.kind == "tool_result" and tool_result_event is None:
                    tool_result_event = event
                    continue
                if event.kind == "thinking" and thinking_event is None:
                    thinking_event = event
                    continue
                if output_event is None:
                    output_event = event
            if lifecycle_event is not None:
                return AgentActivitySignal(AgentActivityKind.LIFECYCLE, raw=lifecycle_event.text)
            if tool_result_event is not None:
                return AgentActivitySignal(
                    AgentActivityKind.TOOL_RESULT,
                    raw=tool_result_event.text,
                )
            priority_event = output_event or thinking_event
            if priority_event is not None:
                return AgentActivitySignal(AgentActivityKind.OUTPUT_LINE, raw=priority_event.text)
        if self._transcript_parser._current_content_mode == "thinking":
            return None
        return super().classify_activity_line(line)

    def classify_quiet(
        self,
        handle: LiveDescendantHandle,
        liveness_probe: LivenessProbe,
    ) -> AgentExecutionState:
        del handle, liveness_probe
        return AgentExecutionState.ACTIVE

    def supports_session_continuation(self) -> bool:
        return True


class AgyExecutionStrategy(CompletionEnforcingStrategy, GenericExecutionStrategy):
    __slots__ = ()

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        return _classify_agy_activity(line) or super().classify_activity_line(line)

    def supports_incomplete_exit_reprompt(self) -> bool:
        return True


class CursorExecutionStrategy(CompletionEnforcingStrategy, GenericExecutionStrategy):
    __slots__ = ()

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        return _classify_cursor_activity(line) or super().classify_activity_line(line)


class KimiExecutionStrategy(CompletionEnforcingStrategy, GenericExecutionStrategy):
    __slots__ = ()

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        return _classify_kimi_activity(line) or super().classify_activity_line(line)


class PiExecutionStrategy(CompletionEnforcingStrategy, GenericExecutionStrategy):
    __slots__ = ()

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        return _classify_pi_activity(line) or super().classify_activity_line(line)


def _classify_agy_activity(line: str) -> AgentActivitySignal | None:
    obj = loads_json_object(line)
    if obj is None or obj.get("event") != "step_update":
        return None
    raw_step = obj.get("step_update")
    step_update = as_object_dict(raw_step)
    if step_update is None:
        return None
    if step_update.get("step_type") not in {"tool", "subagent"}:
        return None
    if step_update.get("state") == "DONE":
        return AgentActivitySignal(AgentActivityKind.TOOL_RESULT, raw=line)
    return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line)


def _classify_cursor_activity(line: str) -> AgentActivitySignal | None:
    obj = loads_json_object(line)
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


def _classify_kimi_activity(line: str) -> AgentActivitySignal | None:
    obj = loads_json_object(line)
    if obj is None:
        return None
    if obj.get("role") == "tool":
        return AgentActivitySignal(AgentActivityKind.TOOL_RESULT, raw=line)
    tool_calls = obj.get("tool_calls")
    if obj.get("role") == "assistant" and isinstance(tool_calls, list) and tool_calls:
        return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line)
    return None


def _classify_pi_activity(line: str) -> AgentActivitySignal | None:
    obj = loads_json_object(line)
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
    event = as_object_dict(obj.get("assistantMessageEvent"))
    if event is None:
        return None
    if event.get("type") == "toolcall_end":
        return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line)
    if event.get("type") == "error":
        return AgentActivitySignal(
            AgentActivityKind.ERROR_LINE, raw=str(event.get("reason", "error"))
        )
    return None


def _pi_result_text(result: object) -> str:
    if isinstance(result, str):
        return result
    result_dict = as_object_dict(result)
    if result_dict is None:
        return str(result)
    content_value: object = result_dict.get("content")
    if not isinstance(content_value, list):
        return str(result)
    parts: list[str] = []
    for raw_block in content_value:
        block = as_object_dict(raw_block)
        if block is None or str(block.get("type")) != "text":
            continue
        text: object = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)
