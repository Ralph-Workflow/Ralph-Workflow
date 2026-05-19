from __future__ import annotations

from ralph.mcp.server.factory import McpServerHandle
from tests.test_parallel_coordinator_helper__recordedhandle import _RecordedHandle


class _RecordingMcpFactory:
    def __init__(self) -> None:
        self.sessions: list[object] = []
        self.handles: list[_RecordedHandle] = []

    def build(self, session: object) -> McpServerHandle:
        self.sessions.append(session)
        recorded = _RecordedHandle(
            handle=McpServerHandle(
                endpoint=f"http://127.0.0.1:{10_000 + len(self.handles)}/mcp",
                pid=1000 + len(self.handles),
                shutdown=lambda: None,
            )
        )

        def _shutdown(record: _RecordedHandle = recorded) -> None:
            record.shutdown_calls += 1

        recorded.handle = McpServerHandle(
            endpoint=recorded.handle.endpoint,
            pid=recorded.handle.pid,
            shutdown=_shutdown,
        )
        self.handles.append(recorded)
        return recorded.handle
