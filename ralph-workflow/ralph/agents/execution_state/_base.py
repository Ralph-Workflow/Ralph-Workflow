
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
    if not is_prompt_echo_line(queued_line, input_prompt):
        return activity_signal
    return AgentActivitySignal(
        kind=activity_signal.kind,
        raw=activity_signal.raw,
        error_message=activity_signal.error_message,
        is_harness_echo=True,
    )


class BaseExecutionStrategy:

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
            try:
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
        return False
