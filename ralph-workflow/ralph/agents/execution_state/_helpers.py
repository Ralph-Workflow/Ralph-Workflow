from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ralph.agents.activity import AgentActivityKind, AgentActivitySignal
from ralph.process.child_liveness import classify_child_snapshot

from .agent_execution_state import AgentExecutionState

if TYPE_CHECKING:
    from ralph.agents.completion_signals import CompletionSignals
    from ralph.process.child_liveness import ChildLivenessRegistry
    from ralph.process.liveness import LivenessProbe

    from ._live_descendant_handle import _LiveDescendantHandle


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


_OPENCODE_CHILD_SPAWN_TYPES = frozenset({"child_started", "child.spawned"})
_OPENCODE_CHILD_PROGRESS_TYPES = frozenset(
    {"child_progress", "progress", "tool_call", "writing_artifact"}
)
_OPENCODE_CHILD_HEARTBEAT_TYPES = frozenset({"child_heartbeat", "heartbeat"})
_OPENCODE_CHILD_TERMINAL_TYPES = frozenset({"child_complete", "child_failed", "child.terminal"})


_OPENCODE_CHILD_KIND: dict[str, AgentActivityKind] = {
    **dict.fromkeys(_OPENCODE_CHILD_SPAWN_TYPES, AgentActivityKind.CHILD_PROCESS),
    **dict.fromkeys(_OPENCODE_CHILD_PROGRESS_TYPES, AgentActivityKind.CHILD_PROGRESS),
    **dict.fromkeys(_OPENCODE_CHILD_HEARTBEAT_TYPES, AgentActivityKind.CHILD_HEARTBEAT),
    **dict.fromkeys(_OPENCODE_CHILD_TERMINAL_TYPES, AgentActivityKind.CHILD_TERMINAL_ACK),
}


def _classify_opencode_child_signal(line: str) -> AgentActivitySignal | None:
    """Return a child-lifecycle ActivitySignal when the line is a recognised OpenCode event."""
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
    if kind is None:
        return None
    return AgentActivitySignal(kind, raw=line)


def _route_opencode_line_to_registry(
    line: str,
    registry: ChildLivenessRegistry,
    scope_prefix: str,
) -> None:
    """Parse an OpenCode NDJSON line and route lifecycle events into the registry."""
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
    child_id = str(obj.get("child_id") or obj.get("id") or "")
    if not child_id:
        return
    if event_type in _OPENCODE_CHILD_SPAWN_TYPES:
        pid_raw = obj.get("pid")
        pid = int(pid_raw) if isinstance(pid_raw, int | float) else None
        registry.register_child(child_id, scope_prefix, pid=pid)
    elif event_type in _OPENCODE_CHILD_PROGRESS_TYPES:
        phase = str(obj.get("phase")) if obj.get("phase") is not None else None
        registry.record_progress(child_id, phase=phase)
    elif event_type in _OPENCODE_CHILD_HEARTBEAT_TYPES:
        registry.record_heartbeat(child_id)
    elif event_type in _OPENCODE_CHILD_TERMINAL_TYPES:
        terminal_state = str(obj.get("terminal_state", "complete"))
        registry.record_terminal_ack(child_id, terminal_state=terminal_state)


def _check_signals_terminal(completion_signals: CompletionSignals) -> bool:
    try:
        if completion_signals.terminal_ack_seen:
            return True
    except AttributeError:
        pass
    try:
        if completion_signals.artifact_optional:
            return True
    except AttributeError:
        pass
    try:
        return bool(
            completion_signals.explicit_complete or completion_signals.required_artifact_present
        )
    except Exception:
        return False


def _os_descendant_state(
    handle: _LiveDescendantHandle,
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
    """Returns (decided_state_or_none, scoped_evidence_stale)."""
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
    """Returns (decided_state_or_none, scoped_evidence_stale)."""
    scope_prefix = label_prefix if label_prefix is not None else ""
    had_scoped_records = registry.has_records(scope_prefix)
    try:
        reg_snap = registry.snapshot(scope_prefix)
        verdict = classify_child_snapshot(reg_snap)
        if verdict.all_children_terminal:
            return AgentExecutionState.TERMINAL_COMPLETE, False
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
    """Returns (decided_state_or_none, scoped_evidence_stale)."""
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
    handle: _LiveDescendantHandle,
    completion_signals: CompletionSignals,
    liveness_probe: LivenessProbe | None,
    label_prefix: str | None,
    *,
    registry: ChildLivenessRegistry | None = None,
) -> AgentExecutionState:
    """Evidence-precedence exit classification.

    Priority:
      1. terminal_ack_seen or required_artifact_present -> TERMINAL_COMPLETE
      2. registry: all children acked with no remaining active -> TERMINAL_COMPLETE
      3. registry: deferral_allowed -> WAITING_ON_CHILD
      4. probe: deferral_allowed -> WAITING_ON_CHILD
      5. scoped Ralph child evidence exists but is stale -> RESUMABLE_CONTINUE
      6. OS descendants (only when no scoped Ralph evidence exists at all) -> WAITING_ON_CHILD
      7. else -> RESUMABLE_CONTINUE
    """
    if _check_signals_terminal(completion_signals):
        return AgentExecutionState.TERMINAL_COMPLETE

    stale = False

    if registry is not None:
        state, reg_stale = _registry_check_for_exit(registry, label_prefix)
        if state is not None:
            return state
        stale = reg_stale

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


def _classify_claude_json_object(
    obj: dict[str, object],
    raw: str,
) -> AgentActivitySignal:
    event_obj = obj.get("event")
    if obj.get("type") == "stream_event" and isinstance(event_obj, dict):
        return _classify_claude_json_object(cast("dict[str, object]", event_obj), raw)

    event_type = str(obj.get("type", ""))
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
