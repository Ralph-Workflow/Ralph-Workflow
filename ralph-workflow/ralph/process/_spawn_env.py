"""Sanitize inherited environment variables that harm descendant processes."""

from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping

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


def child_env_for_spawn(
    env: Mapping[str, str] | None,
    *,
    allow_activity_relay_controls: bool = False,
) -> dict[str, str] | None:
    """Return the environment map a spawned child actually receives.

    ``None`` means "inherit the parent environment" and is passed through
    unchanged. Otherwise the caller's map is copied and, unless the caller is
    the parent-owned standalone MCP bootstrap, stripped of the private
    activity-relay controls.

    Spawn-argument validation runs against this map rather than the caller's:
    a variable removed here can never reach the child, so it can never poison
    it, and rejecting a spawn over it would refuse a launch that would have
    succeeded.
    """
    if env is None:
        return None
    child_env = dict(env)
    if allow_activity_relay_controls:
        return child_env
    return scrub_activity_relay_controls(child_env)


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
