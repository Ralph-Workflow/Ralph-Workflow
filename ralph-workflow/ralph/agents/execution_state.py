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

    Exit classification requires explicit completion signals (artifact
    present or explicit_complete flag) before declaring terminal success.
    When completion signals are absent, the LivenessProbe and
    handle.has_live_descendants() are consulted to avoid false-positive
    RESUMABLE_CONTINUE verdicts when child agents are still running.

    ``label_scope`` narrows the liveness check to processes whose labels
    start with ``agent:{label_scope}:``.  When None the check falls back to
    the global ``agent:`` prefix (production default).
    """

    def __init__(self, *, label_scope: str | None = None) -> None:
        self._label_scope = label_scope

    def _active_label_prefix(self) -> str:
        if self._label_scope is not None:
            return f"{_AGENT_LABEL_PREFIX}{self._label_scope}:"
        return _AGENT_LABEL_PREFIX

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        """Classify OpenCode output for idle-watchdog activity."""
        return _non_blank_output_signal(line)

    def classify_quiet(
        self,
        handle: _LiveDescendantHandle,
        liveness_probe: LivenessProbe,
    ) -> AgentExecutionState:
        # Check for Ralph-tracked parallel agent workers (label prefix "agent:")
        try:
            if bool(liveness_probe.any_agent_active(self._active_label_prefix())):
                return AgentExecutionState.WAITING_ON_CHILD
        except Exception:
            pass
        # Fall back to psutil-based descendant check for non-Ralph-tracked child processes
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
        # Fast path: strong completion signals always take precedence
        try:
            if completion_signals.explicit_complete or completion_signals.required_artifact_present:
                return AgentExecutionState.TERMINAL_COMPLETE
        except Exception:
            pass
        # Deferred path: wait for child agents if LivenessProbe reports active agents
        if liveness_probe is not None:
            try:
                if bool(liveness_probe.any_agent_active(self._active_label_prefix())):
                    return AgentExecutionState.WAITING_ON_CHILD
            except Exception:
                pass
        # Fall back to OS-level descendant check
        if hasattr(handle, "has_live_descendants"):
            try:
                if bool(handle.has_live_descendants()):
                    return AgentExecutionState.WAITING_ON_CHILD
            except Exception:
                pass
        return AgentExecutionState.RESUMABLE_CONTINUE

    def supports_session_continuation(self) -> bool:
        return True


def strategy_for_transport(
    transport: object,
) -> GenericExecutionStrategy | OpenCodeExecutionStrategy:
    """Return the appropriate ExecutionStrategy for an agent transport."""
    from ralph.config.enums import AgentTransport  # noqa: PLC0415

    if transport == AgentTransport.OPENCODE:
        return OpenCodeExecutionStrategy()
    if transport == AgentTransport.CLAUDE:
        return ClaudeExecutionStrategy()
    return GenericExecutionStrategy()


def _non_blank_output_signal(line: str) -> AgentActivitySignal | None:
    if not line.strip():
        return None
    return AgentActivitySignal(AgentActivityKind.OUTPUT_LINE, raw=line)


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
    "strategy_for_transport",
]
