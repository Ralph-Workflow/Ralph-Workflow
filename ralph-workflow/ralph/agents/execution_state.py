"""Transport-aware execution state model for agent lifecycle management.

Provides AgentExecutionState (active/waiting/resumable/terminal),
the ExecutionStrategy protocol, and concrete GenericExecutionStrategy and
OpenCodeExecutionStrategy implementations.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.agents.completion_signals import CompletionSignals
    from ralph.process.liveness import LivenessProbe


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

    def classify_quiet(
        self,
        handle: object,
        liveness_probe: object,
    ) -> AgentExecutionState:
        if hasattr(handle, "has_live_descendants"):
            try:
                if bool(handle.has_live_descendants()):  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
                    return AgentExecutionState.WAITING_ON_CHILD
            except Exception:
                pass
        return AgentExecutionState.ACTIVE

    def classify_exit(
        self,
        handle: object,
        completion_signals: object,
        liveness_probe: LivenessProbe | None = None,
    ) -> AgentExecutionState:
        return AgentExecutionState.TERMINAL_COMPLETE

    def supports_session_continuation(self) -> bool:
        return False


_AGENT_LABEL_PREFIX = "agent:"


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

    def classify_quiet(
        self,
        handle: object,
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
                if bool(handle.has_live_descendants()):  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
                    return AgentExecutionState.WAITING_ON_CHILD
            except Exception:
                pass
        return AgentExecutionState.ACTIVE

    def classify_exit(
        self,
        handle: object,
        completion_signals: object,
        liveness_probe: LivenessProbe | None = None,
    ) -> AgentExecutionState:
        # Fast path: strong completion signals always take precedence
        try:
            signals: CompletionSignals = completion_signals  # type: ignore[assignment]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
            if signals.explicit_complete or signals.required_artifact_present:
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
                if bool(handle.has_live_descendants()):  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
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
    return GenericExecutionStrategy()


__all__ = [
    "AgentExecutionState",
    "GenericExecutionStrategy",
    "OpenCodeExecutionStrategy",
    "strategy_for_transport",
]
