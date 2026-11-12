from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping

MALLOC_DEBUG_NOISE_VARS: tuple[str, ...] = (
    "MallocStackLogging",
    "MallocStackLoggingNoCompact",
)

INSTALLER_PYTHON_INJECTION_VARS: frozenset[str] = frozenset(
    {
        "PYTHONHOME",
        "PYTHONINSPECT",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONUSERBASE",
    }
)

ACTIVITY_RELAY_CONTROL_ENV_VARS: frozenset[str] = frozenset(
    {
        "RALPH_MCP_ACTIVITY_RELAY_ENDPOINT",
        "RALPH_MCP_ACTIVITY_RELAY_CREDENTIAL",
    }
)
BROKER_SECRET_ENV = "RALPH_BROKER_SECRET"


def scrub_activity_relay_controls[T: MutableMapping[str, str]](env: T) -> T:
    for name in ACTIVITY_RELAY_CONTROL_ENV_VARS:
        env.pop(name, None)
    return env


def child_env_for_spawn(
    env: Mapping[str, str] | None,
    *,
    allow_activity_relay_controls: bool = False,
    allow_broker_secret: bool = False,
    cwd: str | None = None,
) -> dict[str, str]:
    child_env = dict(os.environ if env is None else env)
    if cwd is not None:
        child_env["PWD"] = cwd
        child_env.pop("OLDPWD", None)
    if not allow_broker_secret:
        child_env.pop(BROKER_SECRET_ENV, None)
    if not allow_activity_relay_controls:
        scrub_activity_relay_controls(child_env)
    return child_env


def strip_malloc_debug_noise(env: MutableMapping[str, str]) -> tuple[str, ...]:
    return tuple(name for name in MALLOC_DEBUG_NOISE_VARS if env.pop(name, None) is not None)


def sanitize_process_environment() -> tuple[str, ...]:
    return strip_malloc_debug_noise(os.environ)
