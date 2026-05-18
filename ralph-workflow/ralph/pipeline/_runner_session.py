"""Session capture state for the pipeline runner."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.state import PipelineState


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
    captured_session_id = pop_last_captured_session_id()
    if captured_session_id:
        return state.copy_with(
            last_agent_session_id=captured_session_id,
            session_preserve_retry_pending=False,
        )
    if state.session_preserve_retry_pending is True:
        return state.copy_with(session_preserve_retry_pending=False)
    return state
