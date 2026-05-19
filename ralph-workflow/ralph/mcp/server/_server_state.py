"""Lifecycle state enum for MCP server instances."""

from __future__ import annotations

from enum import StrEnum


class ServerState(StrEnum):
    """Lifecycle state of a running MCP server instance."""

    UNINITIALIZED = "uninitialized"
    RUNNING = "running"
    SHUTDOWN = "shutdown"
