from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.process.child_liveness import classify_child_snapshot

from ._helpers import (
    _AGENT_LABEL_PREFIX,
    _classify_opencode_child_signal,
    _evidence_precedence,
    _non_blank_output_signal,
    _os_descendant_state,
    _probe_check_quiet,
    _route_opencode_line_to_registry,
)
from .agent_execution_state import AgentExecutionState

if TYPE_CHECKING:
    from ralph.agents.activity import AgentActivitySignal
    from ralph.agents.completion_signals import CompletionSignals
    from ralph.process.child_liveness import ChildLivenessRegistry
    from ralph.process.liveness import LivenessProbe

    from ._live_descendant_handle import _LiveDescendantHandle


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

        registry = cast("ChildLivenessRegistry | None", getattr(self, "_registry", None))
        if registry is not None:
            had_scoped_records = registry.has_records(probe_prefix)
            try:
                reg_snap = registry.snapshot(probe_prefix)
                verdict = classify_child_snapshot(reg_snap)
                if verdict.all_children_terminal:
                    return AgentExecutionState.ACTIVE
                if verdict.deferral_allowed:
                    return AgentExecutionState.WAITING_ON_CHILD
                if had_scoped_records:
                    scoped_child_evidence_stale = True
            except Exception:
                pass

        probe_state, probe_stale = _probe_check_quiet(liveness_probe, probe_prefix)
        if probe_state is not None:
            return probe_state
        scoped_child_evidence_stale = scoped_child_evidence_stale or probe_stale

        if scoped_child_evidence_stale:
            return AgentExecutionState.ACTIVE
        return _os_descendant_state(handle, AgentExecutionState.ACTIVE)

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
