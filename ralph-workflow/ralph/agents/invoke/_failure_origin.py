"""Closed attribution vocabulary for agent-invocation failures."""

from typing import Literal

type FailureOrigin = Literal[
    "runtime_launch",
    "intentional_termination",
    "watchdog_observation",
    "mcp_operation",
    "agent",
]

__all__ = ["FailureOrigin"]
