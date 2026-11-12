from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.config.agent_detection import opencode_binary_override
from ralph.config.enums import AgentTransport
from ralph.pipeline.plumbing.smoke_plumbing import (
    _agy_binary_override_env,
    _cursor_binary_override_env,
    _kimi_binary_override_env,
    is_mock_agy_override,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ralph.config.models import AgentConfig, UnifiedConfig

__all__ = [
    "apply_smoke_binary_override",
    "apply_smoke_binary_overrides_to_config",
    "resolve_smoke_binary_override",
    "smoke_binary_override_env_var",
    "smoke_binary_override_transports",
    "smoke_transport_binary",
]


_OVERRIDE_TABLE: Mapping[AgentTransport, tuple[str, Callable[[], str | None], str]] = {
    AgentTransport.AGY: ("RALPH_AGY_BINARY", _agy_binary_override_env, "agy"),
    AgentTransport.CURSOR: ("RALPH_CURSOR_BINARY", _cursor_binary_override_env, "agent"),
    AgentTransport.KIMI: ("RALPH_KIMI_BINARY", _kimi_binary_override_env, "kimi"),
    AgentTransport.OPENCODE: ("RALPH_OPENCODE_BINARY", opencode_binary_override, "opencode"),
}


def smoke_binary_override_transports() -> tuple[AgentTransport, ...]:
    return tuple(_OVERRIDE_TABLE)


def smoke_binary_override_env_var(transport: AgentTransport) -> str | None:
    entry = _OVERRIDE_TABLE.get(transport)
    return None if entry is None else entry[0]


def smoke_transport_binary(transport: AgentTransport, default_binary: str) -> str:
    entry = _OVERRIDE_TABLE.get(transport)
    if entry is None:
        return default_binary
    return entry[1]() or entry[2]


def resolve_smoke_binary_override(transport: AgentTransport) -> str | None:
    entry = _OVERRIDE_TABLE.get(transport)
    if entry is None:
        return None
    env_var, read_override, _default_binary = entry
    override = read_override()
    if not override:
        return None
    resolved = Path(override).expanduser()
    if not resolved.is_absolute():
        resolved = resolved.resolve()
        logger.info(
            "Resolved relative {} '{}' to absolute path '{}'",
            env_var,
            override,
            resolved,
        )
    if shutil.which(str(resolved)) is None and not (
        resolved.is_file() and os.access(resolved, os.X_OK)
    ):
        logger.warning(
            "{} points to '{}', which is not executable; ignoring override",
            env_var,
            override,
        )
        return None
    return str(resolved)


def apply_smoke_binary_override(agent_config: AgentConfig) -> AgentConfig:
    transport = agent_config.transport
    if transport is None:
        return agent_config
    resolved = resolve_smoke_binary_override(transport)
    if resolved is None:
        return agent_config
    env_var = smoke_binary_override_env_var(transport)
    if transport is AgentTransport.AGY and is_mock_agy_override():
        logger.info("mock AGY binary in use: {}", resolved)
    else:
        logger.info("Using {} override: {}", env_var, resolved)
    return agent_config.model_copy(update={"cmd": shlex.quote(resolved)})


def apply_smoke_binary_overrides_to_config(config: UnifiedConfig) -> UnifiedConfig:
    new_agents: dict[str, AgentConfig] = {}
    changed = False
    for name, agent_config in config.agents.items():
        overridden = apply_smoke_binary_override(agent_config)
        changed = changed or overridden is not agent_config
        new_agents[name] = overridden
    if not changed:
        return config
    return config.model_copy(update={"agents": new_agents})
