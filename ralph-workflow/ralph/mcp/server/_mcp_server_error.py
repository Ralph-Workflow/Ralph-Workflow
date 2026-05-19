"""McpServerError — raised when the MCP server fails and restart budget is exhausted."""

from __future__ import annotations


class McpServerError(Exception):
    """Raised when the MCP server fails and the restart budget is exhausted."""

    def __init__(self, message: str, *, restart_count: int) -> None:
        self.restart_count = restart_count
        super().__init__(message)


__all__ = ["McpServerError"]
