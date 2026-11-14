from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, cast

from ralph.agents.activity import AgentActivityKind, AgentActivitySignal
from ralph.mcp.tools.coordination import PROGRESS_PIPELINE_MARKER
from ralph.process.child_liveness import classify_child_snapshot

from .agent_execution_state import AgentExecutionState

if TYPE_CHECKING:
    from ralph.agents.completion_signals import CompletionSignals


if TYPE_CHECKING:
    from ralph.process.child_liveness import ChildLivenessRegistry
    from ralph.process.liveness import LivenessProbe

    from ._live_descendant_handle import LiveDescendantHandle


_AGENT_LABEL_PREFIX = "agent:"


_CLAUDE_LIFECYCLE_EVENTS = frozenset(
    {
        "message_start",
        "message_stop",
        "content_block_start",
        "content_block_stop",
        "message_delta",
        "assistant",
        "user",
        "system",
    }
)


def _non_blank_output_signal(line: str) -> AgentActivitySignal | None:
    if not line.strip():
        return None
    return AgentActivitySignal(AgentActivityKind.OUTPUT_LINE, raw=line)


def _error_message_from_error_field(obj: dict[str, object]) -> str:
    from ralph.agents.parsers.base import extract_error_message

    return extract_error_message(obj)


def _tool_state_error_message(obj: dict[str, object]) -> str | None:
    part = obj.get("part")
    if not isinstance(part, dict):
        return None
    state = cast("dict[str, object]", part).get("state")
    if not isinstance(state, dict):
        return None
    state_dict = cast("dict[str, object]", state)
    if str(state_dict.get("status", "")) != "error":
        return None
    return str(state_dict.get("error", "tool error"))


def _error_output_signal(line: str) -> AgentActivitySignal | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        parsed = cast("object", json.loads(stripped, strict=False))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    obj = cast("dict[str, object]", parsed)
    event_type = str(obj.get("type", ""))
    if event_type == "error":
        return AgentActivitySignal(
            AgentActivityKind.ERROR_LINE, raw=_error_message_from_error_field(obj)
        )
    return None


_PROGRESS_STATUS = re.compile(r"status='([^']*)'")
_PROGRESS_NOTE = re.compile(r"note='([^']*)'")


def _progress_report_signal(line: str) -> AgentActivitySignal | None:
    if PROGRESS_PIPELINE_MARKER not in line:
        return None
    status_match = _PROGRESS_STATUS.search(line)
    note_match = _PROGRESS_NOTE.search(line)
    status = status_match.group(1) if status_match else ""
    note = note_match.group(1) if note_match else ""
    return AgentActivitySignal(
        AgentActivityKind.PROGRESS_REPORT, raw=f"status={status} note={note}"
    )


_OPENCODE_CHILD_SPAWN_TYPES = frozenset({"child_started", "child.spawned"})
_OPENCODE_CHILD_PROGRESS_TYPES = frozenset({"child_progress"})
_OPENCODE_CHILD_HEARTBEAT_TYPES = frozenset({"child_heartbeat"})
_OPENCODE_CHILD_TERMINAL_TYPES = frozenset({"child_complete", "child_failed", "child.terminal"})

_OPENCODE_SCOPED_PROGRESS_TYPES = frozenset({"progress", "tool_call", "writing_artifact"})
_OPENCODE_SCOPED_HEARTBEAT_TYPES = frozenset({"heartbeat"})
_OPENCODE_CHILD_SCOPE_KEYS = ("child_id", "childId", "subagent_id", "subagentId")

_OPENCODE_SUBAGENT_TOOLS = frozenset({"agent", "delegate", "spawn_agent", "subagent", "task"})

_OPENCODE_CHILD_KIND = {
    **dict.fromkeys(_OPENCODE_CHILD_SPAWN_TYPES, AgentActivityKind.CHILD_PROCESS),
    **dict.fromkeys(_OPENCODE_CHILD_PROGRESS_TYPES, AgentActivityKind.CHILD_PROGRESS),
    **dict.fromkeys(_OPENCODE_CHILD_HEARTBEAT_TYPES, AgentActivityKind.CHILD_HEARTBEAT),
    **dict.fromkeys(_OPENCODE_CHILD_TERMINAL_TYPES, AgentActivityKind.CHILD_TERMINAL_ACK),
}

_OPENCODE_TOOL_EVENT_TYPES = frozenset({"tool_use", "tool_result"})


def _opencode_tool_name(obj: dict[str, object]) -> str | None:
    if str(obj.get("type", "")) not in _OPENCODE_TOOL_EVENT_TYPES:
        return None
    part = obj.get("part")
    if not isinstance(part, dict):
        return None
    part_obj = cast("dict[str, object]", part)
    part_type = part_obj.get("type")
    if part_type is not None and part_type != "tool":
        return None
    return str(part_obj.get("tool", "")).strip() or None


def _is_opencode_subagent_tool(tool_name: str | None) -> bool:
    return tool_name is not None and tool_name.lower() in _OPENCODE_SUBAGENT_TOOLS


def _opencode_tool_signal(obj: dict[str, object], line: str) -> AgentActivitySignal | None:
    tool_name = _opencode_tool_name(obj)
    if tool_name is None:
        return None
    tool_error = _tool_state_error_message(obj)
    if _is_opencode_subagent_tool(tool_name):
        part = obj.get("part")
        state = part.get("state") if isinstance(part, dict) else None
        status = state.get("status") if isinstance(state, dict) else None
        if status in {"completed", "error"}:
            return AgentActivitySignal(
                AgentActivityKind.CHILD_TERMINAL_ACK, raw=line, error_message=tool_error
            )
        return AgentActivitySignal(
            AgentActivityKind.CHILD_PROGRESS, raw=line, error_message=tool_error
        )
    if str(obj.get("type", "")) == "tool_result":
        return AgentActivitySignal(
            AgentActivityKind.TOOL_RESULT, raw=line, error_message=tool_error
        )
    return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=line, error_message=tool_error)


_OPENCODE_STEP_FRAME_TYPES = frozenset({"step_start", "step_finish"})


def _opencode_step_frame_signal(line: str) -> AgentActivitySignal | None:
    stripped = line.strip()
    if not stripped.startswith("{"):
        return None
    try:
        parsed = cast("object", json.loads(stripped, strict=False))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    obj = cast("dict[str, object]", parsed)
    if str(obj.get("type", "")) not in _OPENCODE_STEP_FRAME_TYPES:
        return None
    return AgentActivitySignal(AgentActivityKind.LIFECYCLE, raw=line)


def _classify_opencode_child_signal(line: str) -> AgentActivitySignal | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        parsed = cast("object", json.loads(stripped, strict=False))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    obj = cast("dict[str, object]", parsed)
    event_type = str(obj.get("type", ""))
    kind = _OPENCODE_CHILD_KIND.get(event_type)
    if kind is not None:
        return AgentActivitySignal(kind, raw=line)
    scoped_kind = _opencode_scoped_child_kind(obj, event_type)
    if scoped_kind is not None:
        return AgentActivitySignal(scoped_kind, raw=line)
    return _opencode_tool_signal(obj, line)


def _opencode_scoped_child_kind(
    obj: dict[str, object],
    event_type: str,
) -> AgentActivityKind | None:
    if event_type not in _OPENCODE_SCOPED_PROGRESS_TYPES | _OPENCODE_SCOPED_HEARTBEAT_TYPES:
        return None
    if not any(obj.get(key) for key in _OPENCODE_CHILD_SCOPE_KEYS):
        return None
    if event_type in _OPENCODE_SCOPED_HEARTBEAT_TYPES:
        return AgentActivityKind.CHILD_HEARTBEAT
    return AgentActivityKind.CHILD_PROGRESS


_GENERIC_CHILD_PROGRESS_KIND: frozenset[str] = frozenset(
    {
        "child_progress",
        "subagent_progress",
    }
)
_GENERIC_CHILD_HEARTBEAT_KIND: frozenset[str] = frozenset(
    {
        "child_heartbeat",
        "subagent_heartbeat",
        "child_alive",
        "subagent_alive",
    }
)
_GENERIC_CHILD_TERMINAL_KIND: frozenset[str] = frozenset(
    {
        "child_complete",
        "child_failed",
        "child_terminal",
        "child_cancelled",
        "subagent_complete",
        "subagent_failed",
        "subagent_cancelled",
        "subagent_terminal",
    }
)
_GENERIC_CHILD_SCOPE_PREFIXES: tuple[str, ...] = ("child_", "subagent_")
_GENERIC_PROGRESS_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*\[child\]", re.IGNORECASE),
    re.compile(r"^\s*\[subagent\]", re.IGNORECASE),
    re.compile(r"^\s*subagent progress\b", re.IGNORECASE),
    re.compile(r"^\s*child progress\b", re.IGNORECASE),
)
_GENERIC_HEARTBEAT_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*subagent heartbeat\b", re.IGNORECASE),
    re.compile(r"^\s*child heartbeat\b", re.IGNORECASE),
)


def _extract_event_kind_from_json(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        parsed = cast("object", json.loads(stripped, strict=False))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    obj = cast("dict[str, object]", parsed)
    raw_type = obj.get("type")
    if isinstance(raw_type, str) and raw_type:
        return raw_type.strip().lower()
    raw_event = obj.get("event")
    if isinstance(raw_event, str) and raw_event:
        return raw_event.strip().lower()
    return None


def _classify_generic_child_signal(line: str) -> AgentActivitySignal | None:
    if not line.strip():
        return None
    signal = _classify_generic_child_signal_from_json(line)
    if signal is not None:
        return signal
    return _classify_generic_child_signal_from_text(line)


def _classify_generic_child_signal_from_json(
    line: str,
) -> AgentActivitySignal | None:
    event_kind = _extract_event_kind_from_json(line)
    if event_kind is None:
        return None
    if event_kind in _GENERIC_CHILD_TERMINAL_KIND:
        return None
    if event_kind in _GENERIC_CHILD_HEARTBEAT_KIND:
        return AgentActivitySignal(AgentActivityKind.CHILD_HEARTBEAT, raw=line)
    if event_kind in _GENERIC_CHILD_PROGRESS_KIND:
        return AgentActivitySignal(AgentActivityKind.CHILD_PROGRESS, raw=line)
    if event_kind.startswith(_GENERIC_CHILD_SCOPE_PREFIXES):
        return AgentActivitySignal(AgentActivityKind.CHILD_PROGRESS, raw=line)
    return None


def _classify_generic_child_signal_from_text(
    line: str,
) -> AgentActivitySignal | None:
    for pattern in _GENERIC_HEARTBEAT_MARKERS:
        if pattern.match(line):
            return AgentActivitySignal(AgentActivityKind.CHILD_HEARTBEAT, raw=line)
    for pattern in _GENERIC_PROGRESS_MARKERS:
        if pattern.match(line):
            return AgentActivitySignal(AgentActivityKind.CHILD_PROGRESS, raw=line)
    return None


def _opencode_child_id_from_object(obj: dict[str, object]) -> str | None:
    for key in (*_OPENCODE_CHILD_SCOPE_KEYS, "id"):
        value = obj.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    return None


def parse_opencode_child_id(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        parsed = cast("object", json.loads(stripped, strict=False))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    obj = cast("dict[str, object]", parsed)
    native_task_id = _opencode_native_task_call_id(obj)
    if native_task_id is not None:
        return native_task_id
    return _opencode_child_id_from_object(obj)


def _opencode_native_task_call_id(obj: dict[str, object]) -> str | None:
    if not _is_opencode_subagent_tool(_opencode_tool_name(obj)):
        return None
    part = obj.get("part")
    if not isinstance(part, dict):
        return None
    part_obj = cast("dict[str, object]", part)
    for key in ("callID", "callId"):
        call_id = part_obj.get(key)
        if isinstance(call_id, str) and call_id:
            return call_id
    return None


def _route_opencode_line_to_registry(
    line: str,
    registry: ChildLivenessRegistry,
    scope_prefix: str,
) -> None:
    stripped = line.strip()
    if not stripped:
        return
    try:
        parsed = cast("object", json.loads(stripped, strict=False))
    except json.JSONDecodeError:
        return
    if not isinstance(parsed, dict):
        return
    obj = cast("dict[str, object]", parsed)
    event_type = str(obj.get("type", ""))
    if _opencode_native_task_call_id(obj) is not None:
        _route_opencode_native_task(obj, registry, scope_prefix)
        return
    child_id = _opencode_child_id_from_object(obj)
    if child_id is not None:
        _route_opencode_lifecycle_event(obj, registry, scope_prefix, child_id, event_type)


def _opencode_pid(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    if isinstance(value, float) and value.is_integer() and value > 0:
        return int(value)
    return None


def _route_opencode_lifecycle_event(
    obj: dict[str, object],
    registry: ChildLivenessRegistry,
    scope_prefix: str,
    child_id: str,
    event_type: str,
) -> None:
    if event_type in _OPENCODE_CHILD_SPAWN_TYPES:
        pid = _opencode_pid(obj.get("pid"))
        registry.register_child(child_id, scope_prefix, pid=pid)
    elif event_type in _OPENCODE_CHILD_PROGRESS_TYPES:
        phase = str(obj.get("phase")) if obj.get("phase") is not None else None
        registry.record_progress(child_id, phase=phase)
    elif event_type in _OPENCODE_CHILD_HEARTBEAT_TYPES:
        registry.record_heartbeat(child_id)
    elif event_type in _OPENCODE_CHILD_TERMINAL_TYPES:
        terminal_state = str(obj.get("terminal_state", "complete"))
        registry.record_terminal_ack(child_id, terminal_state=terminal_state)


def _route_opencode_native_task(
    obj: dict[str, object],
    registry: ChildLivenessRegistry,
    scope_prefix: str,
) -> None:
    if not _is_opencode_subagent_tool(_opencode_tool_name(obj)):
        return
    part = obj.get("part")
    if not isinstance(part, dict):
        return
    part_obj = cast("dict[str, object]", part)
    call_id = _opencode_native_task_call_id(obj)
    state = part_obj.get("state")
    if call_id is None or not isinstance(state, dict):
        return
    state_obj = cast("dict[str, object]", state)
    status = str(state_obj.get("status", ""))
    if status not in {"pending", "running", "completed", "error"}:
        return
    registry.register_child(call_id, scope_prefix)
    registry.record_progress(call_id, phase=status)
    if status in {"completed", "error"}:
        registry.record_terminal_ack(call_id, terminal_state=status)
        child_session_id = _opencode_native_task_session_id(state_obj)
        if child_session_id is not None:
            registry.record_terminal_ack(child_session_id, terminal_state=status)


def _opencode_native_task_session_id(state: dict[str, object]) -> str | None:
    metadata = state.get("metadata")
    if not isinstance(metadata, dict):
        return None
    session_id = cast("dict[str, object]", metadata).get("sessionId")
    if isinstance(session_id, str) and session_id:
        return session_id
    return None


def _os_descendant_state(
    handle: LiveDescendantHandle,
    default: AgentExecutionState,
) -> AgentExecutionState:
    if hasattr(handle, "has_live_descendants"):
        try:
            if bool(handle.has_live_descendants()):
                return AgentExecutionState.WAITING_ON_CHILD
        except Exception:
            pass
    return default


def _probe_check_quiet(
    liveness_probe: LivenessProbe,
    probe_prefix: str,
) -> tuple[AgentExecutionState | None, bool]:
    try:
        snap = liveness_probe.child_snapshot(probe_prefix)
        verdict = classify_child_snapshot(snap)
        if verdict.deferral_allowed:
            return AgentExecutionState.WAITING_ON_CHILD, False
        return None, snap.has_process
    except Exception:
        try:
            if bool(liveness_probe.any_agent_active(probe_prefix)):
                return AgentExecutionState.WAITING_ON_CHILD, False
        except Exception:
            pass
    return None, False


def _registry_check_for_exit(
    registry: ChildLivenessRegistry,
    label_prefix: str | None,
) -> tuple[AgentExecutionState | None, bool]:
    scope_prefix = label_prefix if label_prefix is not None else ""
    had_scoped_records = registry.has_records(scope_prefix)
    try:
        reg_snap = registry.snapshot(scope_prefix)
        verdict = classify_child_snapshot(reg_snap)
        if verdict.all_children_terminal:
            return None, had_scoped_records
        if verdict.deferral_allowed:
            return AgentExecutionState.WAITING_ON_CHILD, False
        return None, had_scoped_records
    except Exception:
        pass
    return None, False


def _probe_check_exit(
    liveness_probe: LivenessProbe,
    label_prefix: str | None,
) -> tuple[AgentExecutionState | None, bool]:
    prefix = label_prefix if label_prefix is not None else ""
    try:
        snap = liveness_probe.child_snapshot(prefix)
        verdict = classify_child_snapshot(snap)
        if verdict.deferral_allowed:
            return AgentExecutionState.WAITING_ON_CHILD, False
        return None, snap.has_process
    except Exception:
        if label_prefix is not None:
            try:
                if bool(liveness_probe.any_agent_active(label_prefix)):
                    return AgentExecutionState.WAITING_ON_CHILD, False
            except Exception:
                pass
    return None, False


def _evidence_precedence(
    handle: LiveDescendantHandle,
    completion_signals: CompletionSignals,
    liveness_probe: LivenessProbe | None,
    label_prefix: str | None,
    *,
    registry: ChildLivenessRegistry | None = None,
    scoped_evidence_seen: bool = False,
) -> AgentExecutionState:
    from ralph.agents.completion_signals import completion_signals_terminal

    if completion_signals_terminal(completion_signals):
        return AgentExecutionState.TERMINAL_COMPLETE

    stale = scoped_evidence_seen

    if registry is not None:
        state, reg_stale = _registry_check_for_exit(registry, label_prefix)
        if state is not None:
            return state
        stale = stale or reg_stale

    if liveness_probe is not None:
        state, probe_stale = _probe_check_exit(liveness_probe, label_prefix)
        if state is not None:
            return state
        stale = stale or probe_stale

    if stale:
        return AgentExecutionState.RESUMABLE_CONTINUE
    return _os_descendant_state(handle, AgentExecutionState.RESUMABLE_CONTINUE)


def _classify_claude_prefixed_line(stripped: str) -> AgentActivitySignal | None:
    if stripped.startswith("[claude]:"):
        return AgentActivitySignal(AgentActivityKind.LIFECYCLE, raw=stripped)
    if not (stripped.startswith("claude ") or stripped.startswith("claude/")):
        return None
    if " tool: " in stripped:
        return AgentActivitySignal(AgentActivityKind.TOOL_USE, raw=stripped)
    return AgentActivitySignal(AgentActivityKind.LIFECYCLE, raw=stripped)


_CLAUDE_SUBAGENT_TOOLS = frozenset({"task"})


def _claude_nested_tool_kind(obj: dict[str, object]) -> AgentActivityKind | None:
    message = obj.get("message")
    if not isinstance(message, dict):
        return None
    content = cast("dict[str, object]", message).get("content")
    if not isinstance(content, list):
        return None
    for block in cast("list[object]", content):
        if not isinstance(block, dict):
            continue
        block_obj = cast("dict[str, object]", block)
        block_type = str(block_obj.get("type", ""))
        if block_type == "tool_use":
            tool_name = str(block_obj.get("name", "")).strip().lower()
            if tool_name in _CLAUDE_SUBAGENT_TOOLS:
                return AgentActivityKind.CHILD_PROGRESS
            return AgentActivityKind.TOOL_USE
        if block_type == "tool_result":
            return AgentActivityKind.TOOL_RESULT
    return None


def _classify_claude_json_object(
    obj: dict[str, object],
    raw: str,
) -> AgentActivitySignal:
    event_obj = obj.get("event")
    if obj.get("type") == "stream_event" and isinstance(event_obj, dict):
        return _classify_claude_json_object(cast("dict[str, object]", event_obj), raw)

    event_type = str(obj.get("type", ""))
    if event_type in {"assistant", "user"}:
        nested_kind = _claude_nested_tool_kind(obj)
        if nested_kind is not None:
            return AgentActivitySignal(nested_kind, raw=raw)

    kind = _claude_activity_kind_for_event(event_type, obj)
    return AgentActivitySignal(kind, raw=raw)


def _claude_activity_kind_for_event(
    event_type: str,
    obj: dict[str, object],
) -> AgentActivityKind:
    if event_type == "content_block_delta":
        return AgentActivityKind.STREAM_DELTA
    if event_type == "content_block_start":
        return _claude_activity_kind_for_content_block(obj)
    if event_type in {"tool_use", "mcp_tool_call"}:
        return AgentActivityKind.TOOL_USE
    if event_type in {"tool_result", "mcp_tool_result"}:
        return AgentActivityKind.TOOL_RESULT
    if event_type in _CLAUDE_LIFECYCLE_EVENTS:
        return AgentActivityKind.LIFECYCLE
    return AgentActivityKind.OUTPUT_LINE


def _claude_activity_kind_for_content_block(obj: dict[str, object]) -> AgentActivityKind:
    content_block = obj.get("content_block")
    if isinstance(content_block, dict):
        block_type = str(content_block.get("type", ""))
        if block_type == "tool_use":
            return AgentActivityKind.TOOL_USE
        if block_type == "tool_result":
            return AgentActivityKind.TOOL_RESULT
    return AgentActivityKind.LIFECYCLE
