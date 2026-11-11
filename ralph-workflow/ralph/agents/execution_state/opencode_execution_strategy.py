
from __future__ import annotations

import contextlib
import json
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
    _classify_generic_child_signal,
    _classify_opencode_child_signal,
    _error_output_signal,
    _evidence_precedence,
    _non_blank_output_signal,
    _opencode_step_frame_signal,
    _os_descendant_state,
    _probe_check_quiet,
    _progress_report_signal,
    _route_opencode_line_to_registry,
    parse_opencode_child_id,
)
from .agent_execution_state import AgentExecutionState

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.activity import AgentActivitySignal
    from ralph.agents.completion_signals import CompletionSignals
    from ralph.process.child_liveness import ChildLivenessRegistry
    from ralph.process.liveness import LivenessProbe

    from ._live_descendant_handle import LiveDescendantHandle


def _step_frame_message_id(envelope: dict[str, object]) -> str | None:
    part = envelope.get("part")
    if not isinstance(part, dict):
        return None
    message_id = cast("dict[str, object]", part).get("messageID")
    if isinstance(message_id, str) and message_id:
        return message_id
    return None


class OpenCodeExecutionStrategy(BaseExecutionStrategy):

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
        self._subagent_activity_sink = subagent_activity_sink
        self._scoped_records_seen = False
        self._open_step_messages: set[str] = (
            set()
        )
        self._anonymous_open_steps = 0

    def _active_label_prefix(self) -> str | None:
        if self._label_scope is None:
            return None
        return f"{_AGENT_LABEL_PREFIX}{self._label_scope}:"

    def classify_activity_line(self, line: str) -> AgentActivitySignal | None:
        signal = _classify_opencode_child_signal(line)
        if signal is not None:
            return signal
        error_signal = _error_output_signal(line)
        if error_signal is not None:
            return error_signal
        frame_signal = _opencode_step_frame_signal(line)
        if frame_signal is not None:
            return frame_signal
        progress_signal = _progress_report_signal(line)
        if progress_signal is not None:
            return progress_signal
        return _non_blank_output_signal(line)

    def observe_line(self, line: str) -> None:
        self._update_open_step_liveness(line)
        registry = cast(
            "ChildLivenessRegistry | None", getattr(self, "_registry", None)
        )
        child_id: str | None = parse_opencode_child_id(line)
        if self._subagent_activity_sink is not None or _has_subagent_sink():
            signal = _classify_opencode_child_signal(line)
            if signal is None:
                signal = _classify_generic_child_signal(line)
            if signal is not None and signal.kind in (
                AgentActivityKind.CHILD_PROGRESS,
                AgentActivityKind.CHILD_HEARTBEAT,
            ):
                if _classify_opencode_child_signal(line) is not None:
                    if child_id is not None:
                        if self._subagent_activity_sink is not None:
                            with contextlib.suppress(Exception):
                                self._subagent_activity_sink(line)
                        else:
                            _invoke_subagent_sink(line)
                elif self._subagent_activity_sink is not None:
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

    def record_native_child_progress(self, child_id: str) -> None:
        registry = self._registry
        if registry is None:
            return
        prefix = self._active_label_prefix() or ""
        if not registry.has_child(child_id):
            registry.register_child(child_id, prefix, phase="running")
        registry.record_progress(child_id, phase="running")
        self._scoped_records_seen = True

    @property
    def open_step_count(self) -> int:
        return len(self._open_step_messages) + self._anonymous_open_steps

    def observe_stream_end(self) -> None:
        self._open_step_messages.clear()
        self._anonymous_open_steps = 0

    def _update_open_step_liveness(self, line: str) -> None:
        try:
            decoded = cast("object", json.loads(line))
        except json.JSONDecodeError:
            return
        if not isinstance(decoded, dict):
            return
        envelope = cast("dict[str, object]", decoded)
        event_type = envelope.get("type")
        if event_type not in {"step_start", "step_finish"}:
            return
        message_id = _step_frame_message_id(envelope)
        if event_type == "step_start":
            if message_id is None:
                self._anonymous_open_steps += 1
            else:
                self._open_step_messages.add(message_id)
        elif message_id is None:
            self._anonymous_open_steps = max(0, self._anonymous_open_steps - 1)
        else:
            self._open_step_messages.discard(message_id)

    def classify_quiet(
        self,
        handle: LiveDescendantHandle,
        liveness_probe: LivenessProbe,
    ) -> AgentExecutionState:
        if self.open_step_count > 0:
            return AgentExecutionState.WAITING_ON_CHILD
        prefix = self._active_label_prefix()
        probe_prefix = prefix if prefix is not None else ""

        scoped_child_evidence_stale = False

        registry = cast(
            "ChildLivenessRegistry | None", getattr(self, "_registry", None)
        )
        if registry is not None:
            had_scoped_records = registry.has_records(probe_prefix)
            if had_scoped_records:
                self._scoped_records_seen = True
            if registry.has_active_phase(probe_prefix, "running"):
                running_snapshot = registry.snapshot(probe_prefix)
                running_verdict = classify_child_snapshot(running_snapshot)
                if running_verdict.deferral_allowed:
                    return AgentExecutionState.WAITING_ON_CHILD
            try:
                reg_snap = registry.snapshot(probe_prefix)
                verdict = classify_child_snapshot(reg_snap)
                if verdict.all_children_terminal or (
                    not verdict.deferral_allowed and had_scoped_records
                ):
                    return AgentExecutionState.ACTIVE
                if verdict.deferral_allowed:
                    return AgentExecutionState.WAITING_ON_CHILD
            except Exception:
                pass

        probe_state, probe_stale = _probe_check_quiet(liveness_probe, probe_prefix)
        if probe_state is not None:
            return probe_state
        scoped_child_evidence_stale = scoped_child_evidence_stale or probe_stale

        return (
            AgentExecutionState.ACTIVE
            if scoped_child_evidence_stale or self._scoped_records_seen
            else _os_descendant_state(handle, AgentExecutionState.ACTIVE)
        )

    def classify_exit(
        self,
        handle: LiveDescendantHandle,
        completion_signals: CompletionSignals,
        liveness_probe: LivenessProbe | None = None,
    ) -> AgentExecutionState:
        registry = cast(
            "ChildLivenessRegistry | None", getattr(self, "_registry", None)
        )
        label_prefix = self._active_label_prefix()
        if self._scoped_records_seen:
            return _evidence_precedence(
                handle,
                completion_signals,
                liveness_probe,
                label_prefix,
                registry=registry,
                scoped_evidence_seen=True,
            )
        return _evidence_precedence(
            handle, completion_signals, liveness_probe, label_prefix, registry=registry
        )

    def supports_session_continuation(self) -> bool:
        return True

    def supports_completion_enforcement(self) -> bool:
        return False
