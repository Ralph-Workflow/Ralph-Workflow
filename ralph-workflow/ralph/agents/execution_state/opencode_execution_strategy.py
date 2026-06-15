"""Execution strategy for Opencode agents."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, cast

from ralph.agents.activity import AgentActivityKind
from ralph.mcp.server._activity_sink import (
    get_subagent_sink as _has_subagent_sink,
)
from ralph.mcp.server._activity_sink import (
    invoke_subagent_sink as _invoke_subagent_sink,
)
from ralph.process.child_liveness import classify_child_snapshot

from ._base import BaseExecutionStrategy
from ._helpers import (
    _AGENT_LABEL_PREFIX,
    _classify_opencode_child_signal,
    _error_output_signal,
    _evidence_precedence,
    _non_blank_output_signal,
    _os_descendant_state,
    _probe_check_quiet,
    _progress_report_signal,
    _route_opencode_line_to_registry,
)
from .agent_execution_state import AgentExecutionState

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.activity import AgentActivitySignal
    from ralph.agents.completion_signals import CompletionSignals
    from ralph.process.child_liveness import ChildLivenessRegistry
    from ralph.process.liveness import LivenessProbe

    from ._live_descendant_handle import _LiveDescendantHandle


class OpenCodeExecutionStrategy(BaseExecutionStrategy):
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
        subagent_activity_sink: Callable[[str], None] | None = None,
        **_kwargs: object,
    ) -> None:
        self._label_scope = label_scope
        self._registry = registry
        # Optional sink invoked from ``observe_line`` when a child
        # progress / heartbeat / tool_call signal is observed. The
        # canonical sink is the per-run watchdog's
        # ``record_subagent_work`` method, which updates the
        # per-channel ``_last_subagent_progress_at`` timestamp the
        # verdict hook consults to defer NO_OUTPUT_DEADLINE. The
        # default is None (legacy / non-opencode transports) so
        # existing callers and tests are unaffected.
        self._subagent_activity_sink = subagent_activity_sink
        # Tracks whether this invocation ever observed scoped Ralph
        # child evidence (registry records matching the active label
        # prefix). Once true, the strategy treats the later absence of
        # fresh scoped evidence as stale evidence, preventing raw OS
        # descendant existence alone from keeping a quiet run in
        # WAITING_ON_CHILD after the scoped child has been pruned.
        self._scoped_records_seen = False

    def _active_label_prefix(self) -> str | None:
        if self._label_scope is None:
            return None
        return f"{_AGENT_LABEL_PREFIX}{self._label_scope}:"

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        """Classify OpenCode output for idle-watchdog activity."""
        signal = _classify_opencode_child_signal(line)
        if signal is not None:
            return signal
        progress_signal = _progress_report_signal(line)
        if progress_signal is not None:
            return progress_signal
        error_signal = _error_output_signal(line)
        if error_signal is not None:
            return error_signal
        return _non_blank_output_signal(line)

    def observe_line(self, line: str) -> None:
        """Route a parsed output line into the child liveness registry.

        When ``subagent_activity_sink`` is set, the sink is invoked once
        per CHILD_PROGRESS or CHILD_HEARTBEAT signal so the idle
        watchdog's per-channel evidence surface stays fresh. The
        child_liveness registry continues to own freshness tracking;
        this is a thin shim from "progress observed" to "activity
        signal sent". Sink exceptions are swallowed so a buggy sink
        cannot corrupt the registry or break the line loop.
        """
        registry = cast("ChildLivenessRegistry | None", getattr(self, "_registry", None))
        # Invoke the activity sink BEFORE the registry update so a
        # progress signal is recorded as activity regardless of whether
        # the registry update succeeds. We only invoke on the two
        # 'demonstrable work' kinds — CHILD_PROGRESS (phase change or
        # tool_call) and CHILD_HEARTBEAT (live signal). Terminal and
        # spawn signals are not forward progress: a child_complete
        # event means the child is no longer running, and child_started
        # is just OS-level evidence the child was launched.
        #
        # Two sink sources are consulted: the constructor-injected
        # ``subagent_activity_sink`` (used by direct unit tests that
        # construct a strategy with an explicit sink) and the
        # per-task contextvar (production: the per-run watchdog
        # registers itself before its lines loop starts). The
        # constructor sink takes precedence so test fixtures can
        # override the production wiring.
        if self._subagent_activity_sink is not None or _has_subagent_sink():
            signal = _classify_opencode_child_signal(line)
            if signal is not None and signal.kind in (
                AgentActivityKind.CHILD_PROGRESS,
                AgentActivityKind.CHILD_HEARTBEAT,
            ):
                if self._subagent_activity_sink is not None:
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
            if had_scoped_records:
                self._scoped_records_seen = True
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

        # Once scoped Ralph child evidence has been observed for this
        # invocation, its later absence (records pruned due to staleness,
        # or probe returning no fresh evidence) must not be overridden by
        # raw OS descendant existence. Raw descendants alone are only a
        # valid WAITING_ON_CHILD signal when Ralph never had scoped
        # visibility into the child in the first place.
        if scoped_child_evidence_stale or self._scoped_records_seen:
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

    def supports_completion_enforcement(self) -> bool:
        return False
