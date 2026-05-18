"""McpAuditEventType — enumeration of MCP audit event categories."""

from __future__ import annotations

from enum import StrEnum


class McpAuditEventType(StrEnum):
    """Enumeration of MCP audit event categories."""

    TOOL = "tool"
    DENIAL = "denial"
    MODE_TRANSITION = "mode_transition"
    HEARTBEAT = "heartbeat"
    SELF_TERMINATION = "self_termination"


__all__ = ["McpAuditEventType"]
