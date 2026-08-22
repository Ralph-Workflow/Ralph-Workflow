"""Sanitize inherited environment variables that harm descendant processes."""

from __future__ import annotations

import os
from collections.abc import MutableMapping

MALLOC_DEBUG_NOISE_VARS: tuple[str, ...] = (
    "MallocStackLogging",
    "MallocStackLoggingNoCompact",
)

# Parent-owned conflict-resolution relay controls. These must never reach an
# agent-controlled child. The standalone MCP server receives them only during
# bootstrap and immediately removes them after constructing its sender.
ACTIVITY_RELAY_CONTROL_ENV_VARS: frozenset[str] = frozenset(
    {
        "RALPH_MCP_ACTIVITY_RELAY_ENDPOINT",
        "RALPH_MCP_ACTIVITY_RELAY_CREDENTIAL",
    }
)


def scrub_activity_relay_controls[T: MutableMapping[str, str]](env: T) -> T:
    """Remove parent-owned activity-relay controls in place and return the same map."""
    for name in ACTIVITY_RELAY_CONTROL_ENV_VARS:
        env.pop(name, None)
    return env


def strip_malloc_debug_noise(env: MutableMapping[str, str]) -> tuple[str, ...]:
    """Remove inherited macOS malloc-stack-logging toggles from ``env``."""
    return tuple(name for name in MALLOC_DEBUG_NOISE_VARS if env.pop(name, None) is not None)


def sanitize_process_environment() -> tuple[str, ...]:
    """Remove inherited malloc-debug toggles before this process launches descendants.

    Activity-relay controls are scrubbed at each agent-controlled child spawn,
    not from the standalone MCP server's own bootstrap environment: that server
    must read its one-time relay controls before it can remove them.
    """
    return strip_malloc_debug_noise(os.environ)
