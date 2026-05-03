"""Transport-aware execution state model for agent lifecycle management.

Provides AgentExecutionState (active/waiting/resumable/terminal),
the ExecutionStrategy protocol, and concrete GenericExecutionStrategy and
OpenCodeExecutionStrategy implementations.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import TYPE_CHECKING, cast

from ralph.agents.activity import AgentActivityKind, AgentActivitySignal

if TYPE_CHECKING:
    from typing import Protocol

    from ralph.agents.completion_signals import CompletionSignals
    from ralph.process.child_liveness import ChildLivenessRegistry
    from ralph.process.liveness import LivenessProbe

    class _LiveDescendantHandle(Protocol):
        def has_live_descendants(self) -> bool: ...


class AgentExecutionState(StrEnum):
    """Execution state for an agent run."""

    ACTIVE = "active"
    WAITING_ON_CHILD = "waiting_on_child"
    RESUMABLE_CONTINUE = "resumable_continue"
    TERMINAL_COMPLETE = "terminal_complete"


class GenericExecutionStrategy:
    """Default strategy: single-process lifetime, exit 0 is terminal success.

    Replicates the behaviour that existed before the session-aware model was
    introduced so that Claude/Codex paths are unaffected.
    """

    def observe_line(self, line: str) -> None:
        """Observe a raw provider line for optional strategy-specific state updates."""
        del line

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        """Classify a raw output line for idle-watchdog activity.

        Generic transports treat any non-blank line as activity while rejecting
        whitespace-only heartbeats so a process cannot evade the idle deadline
        without emitting meaningful provider output.
        """
        return _non_blank_output_signal(line)

    def classify_quiet(
        self,
        handle: _LiveDescendantHandle,
        liveness_probe: LivenessProbe,
    ) -> AgentExecutionState:
        if hasattr(handle, "has_live_descendants"):
            try:
                if bool(handle.has_live_descendants()):
                    return AgentExecutionState.WAITING_ON_CHILD
            except Exception:
                pass
        return AgentExecutionState.ACTIVE

    def classify_exit(
        self,
        handle: _LiveDescendantHandle,
        completion_signals: CompletionSignals,
        liveness_probe: LivenessProbe | None = None,
    ) -> AgentExecutionState:
        return AgentExecutionState.TERMINAL_COMPLETE

    def supports_session_continuation(self) -> bool:
        return False


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


class OpenCodeExecutionStrategy:
    """OpenCode-aware strategy.

    Idle classification checks the injectable LivenessProbe before falling
    back to the psutil-based has_live_descendants(), so unit tests can inject
    a FakeLivenessProbe without spawning real processes.

    Exit classification uses evidence precedence:
      1. terminal_ack_seen or schema-valid required artifact -> TERMINAL_COMPLETE
      2. fresh progress in registry -> WAITING_ON_CHILD
      3. live OS descendants with no fresh progress -> RESUMABLE_CONTINUE (stale)
      4. else -> RESUMABLE_CONTINUE

    ``label_scope`` narrows the Ralph-tracked liveness check to processes whose
    labels start with ``agent:{label_scope}:``. When no scope is available,
    the empty-prefix registry-wide snapshot is consulted; the strategy never
    returns ACTIVE based on a never-matching sentinel.
    """

    def __init__(
        self,
        *,
        label_scope: str | None = None,
        registry: ChildLivenessRegistry | None = None,
    ) -> None:
        self._label_scope = label_scope
        self._registry = registry

    def _active_label_prefix(self) -> str | None:
        if self._label_scope is None:
            return None
        return f"{_AGENT_LABEL_PREFIX}{self._label_scope}:"

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        """Classify OpenCode output for idle-watchdog activity."""
        signal = _classify_opencode_child_signal(line)
        if signal is not None:
            return signal
        return _non_blank_output_signal(line)

    def observe_line(self, line: str) -> None:
        """Route a parsed output line into the child liveness registry."""
        registry = cast("ChildLivenessRegistry | None", getattr(self, "_registry", None))
        if registry is None:
            return
        _route_opencode_line_to_registry(line, registry, self._active_label_prefix() or "")

    def classify_quiet(
        self,
        handle: _LiveDescendantHandle,
        liveness_probe: LivenessProbe,
    ) -> AgentExecutionState:
        prefix = self._active_label_prefix()
        probe_prefix = prefix if prefix is not None else ""

        scoped_child_evidence_stale = False

        # Check own registry first for fresh child evidence
        registry = cast("ChildLivenessRegistry | None", getattr(self, "_registry", None))
        if registry is not None:
            result = self._classify_quiet_from_registry(registry, probe_prefix)
            if result is not None:
                return result
            if registry.snapshot(probe_prefix).has_process:
                scoped_child_evidence_stale = True

        # Use child_snapshot if the probe supports it (DefaultLivenessProbe/FakeLivenessProbe)
        try:
            snap = liveness_probe.child_snapshot(probe_prefix)
            if snap.has_fresh_progress or snap.has_fresh_label:
                return AgentExecutionState.WAITING_ON_CHILD
            if snap.has_process:
                scoped_child_evidence_stale = True
        except Exception:
            try:
                if bool(liveness_probe.any_agent_active(probe_prefix)):
                    return AgentExecutionState.WAITING_ON_CHILD
            except Exception:
                pass

        # Only fall back to psutil-based descendant check when there is NO scoped
        # Ralph child evidence at all. When scoped evidence exists but is stale,
        # raw OS descendant presence alone is insufficient to keep deferring timeout.
        if scoped_child_evidence_stale:
            return AgentExecutionState.ACTIVE

        if hasattr(handle, "has_live_descendants"):
            try:
                if bool(handle.has_live_descendants()):
                    return AgentExecutionState.WAITING_ON_CHILD
            except Exception:
                pass
        return AgentExecutionState.ACTIVE

    def _classify_quiet_from_registry(
        self,
        registry: ChildLivenessRegistry,
        probe_prefix: str,
    ) -> AgentExecutionState | None:
        """Check registry for child state; returns state or None if inconclusive."""
        try:
            reg_snap = registry.snapshot(probe_prefix)
            if reg_snap.terminal_count > 0 and reg_snap.active_count == 0:
                return AgentExecutionState.ACTIVE
            if reg_snap.has_fresh_progress or reg_snap.has_fresh_label:
                return AgentExecutionState.WAITING_ON_CHILD
        except Exception:
            pass
        return None

    def classify_exit(
        self,
        handle: _LiveDescendantHandle,
        completion_signals: CompletionSignals,
        liveness_probe: LivenessProbe | None = None,
    ) -> AgentExecutionState:
        registry = cast("ChildLivenessRegistry | None", getattr(self, "_registry", None))
        label_prefix = self._active_label_prefix()
        return _evidence_precedence(
            handle, completion_signals, liveness_probe, label_prefix, registry=registry
        )

    def supports_session_continuation(self) -> bool:
        return True


def strategy_for_transport(
    transport: object,
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
) -> GenericExecutionStrategy | OpenCodeExecutionStrategy:
    """Return the appropriate ExecutionStrategy for an agent transport."""
    from ralph.config.enums import AgentTransport  # noqa: PLC0415

    if transport == AgentTransport.OPENCODE:
        return OpenCodeExecutionStrategy(label_scope=label_scope, registry=registry)
    if transport == AgentTransport.CLAUDE:
        return ClaudeExecutionStrategy()
    return GenericExecutionStrategy()


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
        pid = int(pid_raw) if isinstance(pid_raw, (int, float)) else None
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
        return bool(
            completion_signals.explicit_complete
            or completion_signals.required_artifact_present
        )
    except Exception:
        return False


def _check_registry_state(
    registry: ChildLivenessRegistry,
    label_prefix: str | None,
) -> AgentExecutionState | None:
    try:
        reg_snap = registry.snapshot(label_prefix if label_prefix is not None else "")
        if reg_snap.terminal_count > 0 and reg_snap.active_count == 0:
            return AgentExecutionState.TERMINAL_COMPLETE
        if reg_snap.has_fresh_progress or reg_snap.has_fresh_label:
            return AgentExecutionState.WAITING_ON_CHILD
    except Exception:
        pass
    return None


def _check_probe_state(
    liveness_probe: LivenessProbe,
    label_prefix: str | None,
) -> AgentExecutionState | None:
    prefix = label_prefix if label_prefix is not None else ""
    try:
        snap = liveness_probe.child_snapshot(prefix)
        if snap.has_fresh_progress or snap.has_fresh_label:
            return AgentExecutionState.WAITING_ON_CHILD
        return None
    except Exception:
        pass
    if label_prefix is not None:
        try:
            if bool(liveness_probe.any_agent_active(label_prefix)):
                return AgentExecutionState.WAITING_ON_CHILD
        except Exception:
            pass
    return None


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
      3. registry: has_fresh_progress or has_fresh_label -> WAITING_ON_CHILD
      4. probe: has_fresh_progress or has_fresh_label -> WAITING_ON_CHILD
      5. scoped Ralph child evidence exists but is stale -> RESUMABLE_CONTINUE
      6. OS descendants (only when no scoped Ralph evidence exists at all) -> WAITING_ON_CHILD
      7. else -> RESUMABLE_CONTINUE
    """
    if _check_signals_terminal(completion_signals):
        return AgentExecutionState.TERMINAL_COMPLETE

    scoped_child_evidence_stale = False

    if registry is not None:
        result = _check_registry_state(registry, label_prefix)
        if result is not None:
            return result
        if _registry_has_stale_child(registry, label_prefix):
            scoped_child_evidence_stale = True

    if liveness_probe is not None:
        result = _check_probe_state(liveness_probe, label_prefix)
        if result is not None:
            return result
        if _probe_has_stale_child(liveness_probe, label_prefix):
            scoped_child_evidence_stale = True

    # When scoped Ralph evidence exists but is stale, do not fall back to raw
    # descendants - return RESUMABLE_CONTINUE so the timeout can fire.
    if scoped_child_evidence_stale:
        return AgentExecutionState.RESUMABLE_CONTINUE

    if hasattr(handle, "has_live_descendants"):
        try:
            if bool(handle.has_live_descendants()):
                return AgentExecutionState.WAITING_ON_CHILD
        except Exception:
            pass
    return AgentExecutionState.RESUMABLE_CONTINUE


def _registry_has_stale_child(
    registry: ChildLivenessRegistry,
    label_prefix: str | None,
) -> bool:
    """Return True if registry has children but they're all stale."""
    try:
        prefix = label_prefix if label_prefix is not None else ""
        reg_snap = registry.snapshot(prefix)
        if reg_snap.has_process:
            return not (reg_snap.has_fresh_progress or reg_snap.has_fresh_label)
    except Exception:
        pass
    return False


def _probe_has_stale_child(
    liveness_probe: LivenessProbe,
    label_prefix: str | None,
) -> bool:
    """Return True if probe has children but they're all stale."""
    prefix = label_prefix if label_prefix is not None else ""
    try:
        snap = liveness_probe.child_snapshot(prefix)
        if snap.has_process:
            return not (snap.has_fresh_progress or snap.has_fresh_label)
    except Exception:
        pass
    return False


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


__all__ = [
    "AgentExecutionState",
    "ClaudeExecutionStrategy",
    "GenericExecutionStrategy",
    "OpenCodeExecutionStrategy",
    "_route_opencode_line_to_registry",
    "strategy_for_transport",
]
