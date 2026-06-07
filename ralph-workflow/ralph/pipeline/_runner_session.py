"""Session capture state for the pipeline runner."""

from __future__ import annotations

import threading
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from ralph.pipeline.state import PipelineState


class _EffectExecutorModule(Protocol):
    def pop_last_captured_failure_reason(self) -> str: ...

    def pop_last_captured_reset_tool_registry(self) -> bool: ...


class _SessionCapture(threading.local):
    session_id: str | None = None


_session_capture_local = _SessionCapture()


def set_last_captured_session_id(session_id: str | None) -> None:
    _session_capture_local.session_id = session_id


def pop_last_captured_session_id() -> str | None:
    session_id = _session_capture_local.session_id
    _session_capture_local.session_id = None
    return session_id


def apply_session_capture(state: PipelineState) -> PipelineState:
    """Apply the captured agent-run side-effects to ``state``.

    Pop the latest captured session id, the latest captured failure
    reason, and the latest captured ``reset_tool_registry`` flag from
    the thread-locals in ``effect_executor`` and ``_runner_session``,
    then merge them into ``state`` via ``copy_with``.

    The failure reason and reset_tool_registry are NEW BEHAVIOR
    fields: pre-fix, they were captured to thread-locals but never
    applied to the state, so the resume-vs-create helper at the top
    of the next attempt's ``_invoke_agent_with_recovery`` saw
    stale/empty values and silently fell back to ``fresh``. Now the
    runner applies them to the state so the helper sees the actual
    captured values.
    """
    # Local import to avoid a circular dependency: effect_executor
    # imports from runner.py (transitively), so we cannot import
    # effect_executor at module load time.
    effect_executor = cast(
        "_EffectExecutorModule", import_module("ralph.pipeline.effect_executor")
    )
    pop_last_captured_failure_reason = effect_executor.pop_last_captured_failure_reason
    pop_last_captured_reset_tool_registry = effect_executor.pop_last_captured_reset_tool_registry

    captured_session_id = pop_last_captured_session_id()
    captured_failure_reason = pop_last_captured_failure_reason()
    captured_reset_tool_registry = pop_last_captured_reset_tool_registry()
    new_state = state
    existing_failure_reason = (
        state.last_agent_failure_reason
        if isinstance(state.last_agent_failure_reason, str)
        else ""
    )
    existing_reset_tool_registry = (
        state.last_agent_reset_tool_registry
        if isinstance(state.last_agent_reset_tool_registry, bool)
        else False
    )
    preserve_retry_pending = (
        state.session_preserve_retry_pending
        if isinstance(state.session_preserve_retry_pending, bool)
        else False
    )
    if captured_session_id:
        new_state = new_state.copy_with(
            last_agent_session_id=captured_session_id,
            session_preserve_retry_pending=False,
        )
    if captured_failure_reason or existing_failure_reason:
        new_state = new_state.copy_with(
            last_agent_failure_reason=captured_failure_reason,
        )
    if captured_reset_tool_registry or existing_reset_tool_registry:
        new_state = new_state.copy_with(
            last_agent_reset_tool_registry=captured_reset_tool_registry,
        )
    if new_state is state and preserve_retry_pending is True:
        return state.copy_with(session_preserve_retry_pending=False)
    if new_state.session_preserve_retry_pending is True:
        return new_state.copy_with(session_preserve_retry_pending=False)
    return new_state
