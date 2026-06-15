"""Template-method base for transport-aware execution strategies.

Subclasses MAY override any public method; the defaults here are the
historical single-process semantics shared by GenericExecutionStrategy and
the fallback path for every transport.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._helpers import (
    _error_output_signal,
    _non_blank_output_signal,
    _progress_report_signal,
)
from .agent_execution_state import AgentExecutionState

if TYPE_CHECKING:
    from ralph.agents.activity import AgentActivitySignal
    from ralph.agents.completion_signals import CompletionSignals
    from ralph.process.liveness import LivenessProbe

    from ._live_descendant_handle import _LiveDescendantHandle


class BaseExecutionStrategy:
    """Default strategy: single-process lifetime, exit 0 is terminal success.

    Concrete strategies inherit from this class and override only the methods
    that need transport-specific behaviour. The defaults replicate the behaviour
    that existed before the session-aware model was introduced so that
    Claude/Codex paths are unaffected.

    The constructor accepts arbitrary keyword arguments so that direct class
    references can be stored in the transport-keyed strategy dispatch table;
    the base implementation ignores every extra argument.
    """

    def __init__(self, **kwargs: object) -> None:
        del kwargs

    def observe_line(self, line: str) -> None:
        """Observe a raw provider line for optional strategy-specific state updates."""
        del line

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
        handle: _LiveDescendantHandle,
        liveness_probe: LivenessProbe,
    ) -> AgentExecutionState:
        del liveness_probe
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
        del handle, completion_signals, liveness_probe
        return AgentExecutionState.TERMINAL_COMPLETE

    def supports_session_continuation(self) -> bool:
        return False

    def supports_completion_enforcement(self) -> bool:
        return False
