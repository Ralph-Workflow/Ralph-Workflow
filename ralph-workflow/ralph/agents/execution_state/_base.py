"""Template-method base for transport-aware execution strategies.

Subclasses MAY override any public method; the defaults here are the
historical single-process semantics shared by GenericExecutionStrategy and
the fallback path for every transport.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from ralph.agents.activity import AgentActivityKind, AgentActivitySignal
from ralph.agents.execution_state._harness_echo import is_prompt_echo_line
from ralph.mcp.server._activity_sink import (
    get_subagent_sink as _has_subagent_sink,
)
from ralph.mcp.server._activity_sink import (
    invoke_subagent_sink as _invoke_subagent_sink,
)

from ._helpers import (
    _classify_generic_child_signal,
    _error_output_signal,
    _non_blank_output_signal,
    _progress_report_signal,
)
from .agent_execution_state import AgentExecutionState

if TYPE_CHECKING:
    from ralph.agents.completion_signals import CompletionSignals
    from ralph.process.child_liveness import ChildLivenessRegistry
    from ralph.process.liveness import LivenessProbe
    from ralph.process.monitor import SubagentPidSource

    from ._live_descendant_handle import LiveDescendantHandle


def with_prompt_echo_flag(
    activity_signal: AgentActivitySignal,
    queued_line: str,
    input_prompt: str | None,
) -> AgentActivitySignal:
    """Return ``activity_signal`` with the harness-echo flag when the line echoes the prompt.

    Both line readers route a raw line through this public helper after
    ``classify_activity_line`` so prompt-echo detection stays
    transport-neutral and tests can exercise it without touching private
    methods.
    """
    if not is_prompt_echo_line(queued_line, input_prompt):
        return activity_signal
    return AgentActivitySignal(
        kind=activity_signal.kind,
        raw=activity_signal.raw,
        error_message=activity_signal.error_message,
        is_harness_echo=True,
    )


class BaseExecutionStrategy:
    """Default strategy: single-process lifetime, exit 0 is terminal success.

    Concrete strategies inherit from this class and override only the methods
    that need transport-specific behaviour. Completion-enforced and
    session-capable transports override these defaults through their registered
    strategy or the shared completion mixin.

    For legacy callers that pass arbitrary kwargs (e.g. direct class refs in
    _STRATEGY_DISPATCH), the __init__ only accepts label_scope and registry.
    """

    def __init__(
        self,
        *,
        label_scope: str | None = None,
        registry: ChildLivenessRegistry | None = None,
        subagent_pid_source: SubagentPidSource | None = None,
    ) -> None:
        self._label_scope = label_scope
        self._registry = registry
        # Optional injected SubagentPidSource (the FILTERED PID source
        # from the watchdog's perspective -- ``known_subagent_pids()``
        # returns the set of REAL subagents, NOT the broader
        # ``descendant_snapshot()`` count). When ``self._registry`` is
        # set (OpenCode path), the registry wins. When ``self._registry``
        # is None but ``self._subagent_pid_source`` is set, this is a
        # registry-aware non-OpenCode transport (Claude / Pi / Codex /
        # Gemini / Generic / Agy / Claude-interactive); the filtered
        # count is the canonical signal. When neither is set, the
        # legacy ``LiveDescendantHandle.has_live_descendants()``
        # fallback wins for backward compatibility with non-instrumented
        # tests.
        self._subagent_pid_source = subagent_pid_source

    def observe_line(self, line: str) -> None:
        """Observe a raw provider line and route child signals to the subagent sink.

        The base implementation now applies the cross-transport generic
        child-signal classifier (``_classify_generic_child_signal``) and
        invokes the active subagent activity sink for any line that
        matches a CHILD_PROGRESS or CHILD_HEARTBEAT marker. This makes
        every transport's ``observe_line`` automatically feed the
        watchdog's per-channel evidence surface without each transport
        needing its own classifier.

        Transport-specialised strategies (OpenCode) continue to override
        ``observe_line`` entirely; the base implementation is only
        invoked when a subclass does NOT override the method, so the
        OpenCode path does not double-invoke the sink.

        Sink exceptions are swallowed (same pattern as
        ``opencode_execution_strategy.py``) so a buggy sink cannot break
        the line loop.
        """
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
        """Observe that the provider's stdout reached EOF.

        Evidence that is only meaningful while frames can still arrive (an
        OpenCode turn whose ``step_finish`` is pending) must be released
        here, because ``classify_quiet`` keeps being consulted while a
        conflict-resolution drain waits for scoped child work to end. The
        base strategy tracks no such evidence.
        """
        return

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        """Classify a raw output line for idle-watchdog activity.

        Generic transports treat any non-blank line as activity while rejecting
        whitespace-only heartbeats so a process cannot evade the idle deadline
        without emitting meaningful provider output. JSON error events are
        classified as ERROR_LINE so the repeated-error circuit breaker can detect
        a wedged retry loop.
        """
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
        # R1 (Trustworthy Idle Watchdog spec): the precedence is
        # (a) OpenCode ChildLivenessRegistry first, (b) injected
        # ``SubagentPidSource`` (per-transport factory helpers in
        # ``ralph.process.monitor._subagent_pid_source_providers`` wire
        # this from a ``SubagentPidRegistry``), then (c) the legacy
        # ``handle.has_live_descendants()`` fallback ONLY when neither
        # filtered seam is injected. The registry wins for OpenCode
        # because the OpenCode path already has a
        # ``ChildLivenessSubagentPidSource`` wrapper feeding the
        # registry from structured lifecycle events; the
        # ``SubagentPidSource`` wins for non-OpenCode transports that
        # do not wire a registry but still inject a filtered source.
        # The BROADER ``descendant_snapshot()`` /
        # ``handle.has_live_descendants()`` count is the bug class
        # from the product spec -- it includes shell helpers like
        # ``npm test`` / ``cargo build`` and produced the 2365s
        # indefinite deferral. We MUST NOT consult it when a filtered
        # seam is present.
        if self._registry is not None:
            try:
                # ``snapshot`` returns a ``ChildActivitySnapshot``;
                # count via ``active_count`` (the filtered live-child
                # signal). Empty prefix matches every record so a
                # generic registry without a label scope still
                # produces a meaningful filtered count.
                reg_snapshot = self._registry.snapshot("")
                if reg_snapshot.active_count > 0:
                    return AgentExecutionState.WAITING_ON_CHILD
            except Exception:
                pass
            return AgentExecutionState.ACTIVE
        if self._subagent_pid_source is not None:
            try:
                pids = self._subagent_pid_source.known_subagent_pids()
            except Exception:
                pids = set()
            if pids:
                return AgentExecutionState.WAITING_ON_CHILD
            return AgentExecutionState.ACTIVE
        # Legacy fallback (no registry, no injected source): keep the
        # previous ``has_live_descendants()`` behavior for
        # backward-compatible tests that pre-date the registry seam.
        # Used ONLY when no filtered seam is available so a
        # non-instrumented test (or a transport the R5 wiring has not
        # yet covered) does not regress to "always ACTIVE".
        if hasattr(handle, "has_live_descendants"):
            try:
                if bool(handle.has_live_descendants()):
                    return AgentExecutionState.WAITING_ON_CHILD
            except Exception:
                pass
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
        """Whether a rc=0 exit without completion evidence gets ONE bounded reprompt.

        When True, ``check_process_result`` raises the typed
        ``AgyIncompleteExitError`` (instead of a plain
        ``AgentInvocationError``) so the recovery layer issues exactly
        one automatic fresh-session reprompt carrying the original task
        plus an explicit completion instruction. Only the AGY strategy
        opts in: AGY has no resumable session (so the opencode-style
        session-continuation path cannot apply) and no stable wire-level
        waiting signal (so the objective exit condition is the only
        recoverable signal).
        """
        return False
