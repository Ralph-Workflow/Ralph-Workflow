"""Private helper: _SessionRegistry for fake SSE MCP server."""
from __future__ import annotations

import queue
import uuid
from threading import Lock

from tests.fixtures.fake_sse_mcp_helper__sessionstate import _SessionState


class _SessionRegistry:
    """Manages sessions for the fake SSE MCP server."""

    def __init__(self) -> None:
        self._sessions: dict[str, _SessionState] = {}
        self._lock = Lock()

    def create(self) -> tuple[str, _SessionState]:
        session_id = uuid.uuid4().hex
        state = _SessionState(events=queue.Queue[bytes]())
        with self._lock:
            self._sessions[session_id] = state
        return session_id, state

    def get(self, session_id: str) -> _SessionState | None:
        with self._lock:
            return self._sessions.get(session_id)
