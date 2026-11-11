
from __future__ import annotations

from ralph.mcp.tools.coordination import ToolError


class ExecutionError(ToolError):

    def __init__(self, message: str = "", *, timed_out: bool = False) -> None:
        super().__init__(message)
        self.timed_out = timed_out
