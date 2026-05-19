from __future__ import annotations

from tests.test_mcp_tool_artifact_rollback_execute_ops_with_rollback_helper__session import _Session


class _DrainSession(_Session):
    def __init__(self, drain: str) -> None:
        self.drain = drain
