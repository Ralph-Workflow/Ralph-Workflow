"""Session capture state for the pipeline runner."""

from __future__ import annotations

import threading
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast

from ralph.pipeline.agent_retry_intent import AgentRetryIntent, cleared_agent_retry_intent

if TYPE_CHECKING:
    from ralph.pipeline.state import PipelineState


class _EffectExecutorModule(Protocol):
    def pop_last_captured_retry_intent(self) -> AgentRetryIntent: ...


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

    Pop the latest captured session id and the latest captured retry
    intent from
    the thread-locals in ``effect_executor`` and ``_runner_session``,
    then merge them into ``state`` via ``copy_with``.

    The retry intent is applied atomically so the next attempt does not
    reconstruct resume behavior from split booleans and stale session state.
    """
    # Local import to avoid a circular dependency: effect_executor
    # imports from runner.py (transitively), so we cannot import
    # effect_executor at module load time.
    effect_executor = cast("_EffectExecutorModule", import_module("ralph.pipeline.effect_executor"))
    pop_last_captured_retry_intent = effect_executor.pop_last_captured_retry_intent

    captured_session_id = pop_last_captured_session_id()
    captured_retry_intent = pop_last_captured_retry_intent()
    new_state = state
    if captured_retry_intent.action is not None or captured_retry_intent.skip_same_agent_retries:
        retry_session_id = (
            captured_retry_intent.session_id
            if captured_retry_intent.action in {"resume", "new_session_with_id"}
            else None
        )
        new_state = new_state.copy_with(
            last_agent_session_id=retry_session_id,
            agent_retry_intent=captured_retry_intent,
        )
    elif captured_session_id:
        new_state = new_state.copy_with(
            last_agent_session_id=captured_session_id,
            agent_retry_intent=cleared_agent_retry_intent(),
        )
    elif state.agent_retry_intent.action is not None:
        new_state = new_state.copy_with(
            agent_retry_intent=cleared_agent_retry_intent(),
        )
    return new_state
