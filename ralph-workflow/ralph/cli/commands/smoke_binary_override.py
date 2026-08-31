"""Table-driven ``RALPH_*_BINARY`` overrides for the smoke harness.

The smoke harness lets an operator point a transport at a different
executable -- a wrapper, an alternate live binary, or a deterministic test
stub -- through a per-transport ``RALPH_<TRANSPORT>_BINARY`` environment
variable. This is a SMOKE-ONLY seam: the live pipeline never rewrites
``AgentConfig.cmd`` from these variables (``ralph.config.agent_detection``
uses them only to decide whether a CLI is installed, and
``ralph.agents.invoke`` only to skip an OpenCode model preflight).

The mechanism used to exist as four hand-copied trios of helpers, applied
inconsistently: the shared harness applied only AGY and Cursor, while the
Kimi and OpenCode CLI commands applied theirs to a config the harness then
discarded -- so ``RALPH_OPENCODE_BINARY`` logged "Using ... override" and had
no effect at all.

Everything now flows through :data:`_OVERRIDE_TABLE`, and
:func:`ralph.cli.commands.smoke.smoke_harness_agent_command` applies it in
exactly ONE place. A transport added to the table is handled identically to
every other; a transport that grows an override variable without a table
entry is caught by ``tests/test_cli_harness_parity.py``.
"""

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


#: The complete transport -> (env var, raw reader, default binary) override
#: table. Static dispatch table; the readers are the canonical single-read
#: accessors so no ``RALPH_*`` variable is read outside the sanctioned
#: environment boundary (see ``scripts/verify_drift.sh``).
_OVERRIDE_TABLE: Mapping[AgentTransport, tuple[str, Callable[[], str | None], str]] = {
    AgentTransport.AGY: ("RALPH_AGY_BINARY", _agy_binary_override_env, "agy"),
    AgentTransport.CURSOR: ("RALPH_CURSOR_BINARY", _cursor_binary_override_env, "agent"),
    AgentTransport.KIMI: ("RALPH_KIMI_BINARY", _kimi_binary_override_env, "kimi"),
    AgentTransport.OPENCODE: ("RALPH_OPENCODE_BINARY", opencode_binary_override, "opencode"),
}


def smoke_binary_override_transports() -> tuple[AgentTransport, ...]:
    """Return every transport that honors a ``RALPH_*_BINARY`` override.

    Returns:
        The table's transports, in declaration order.
    """
    return tuple(_OVERRIDE_TABLE)


def smoke_binary_override_env_var(transport: AgentTransport) -> str | None:
    """Return the override environment variable name for ``transport``.

    Args:
        transport: The transport to look up.

    Returns:
        The variable name (e.g. ``"RALPH_KIMI_BINARY"``), or ``None`` when
        the transport has no binary override.
    """
    entry = _OVERRIDE_TABLE.get(transport)
    return None if entry is None else entry[0]


def smoke_transport_binary(transport: AgentTransport, default_binary: str) -> str:
    """Return the binary the smoke preflight should look for on ``PATH``.

    The RAW (unvalidated) override value is returned when the variable is
    set, so a preflight that cannot find it reports the operator's own path
    in the error rather than silently checking the stock binary instead.

    Args:
        transport: The transport whose binary is wanted.
        default_binary: The stock binary name to use when no override is set;
            also used when ``transport`` has no override entry at all.

    Returns:
        The override value, or ``default_binary``.
    """
    entry = _OVERRIDE_TABLE.get(transport)
    if entry is None:
        return default_binary
    return entry[1]() or entry[2]


def resolve_smoke_binary_override(transport: AgentTransport) -> str | None:
    """Return the validated absolute override path for ``transport``, or ``None``.

    A relative override is resolved against the current working directory so
    a downstream :class:`subprocess.Popen` always sees an absolute path. The
    path must resolve to a regular file with executable bits set, or to a
    name :func:`shutil.which` can locate on ``PATH``. When validation fails a
    WARNING is logged and ``None`` is returned, so the caller falls back to
    the transport's real binary on ``PATH``.

    Args:
        transport: The transport whose override should be resolved.

    Returns:
        The absolute override path, or ``None`` when unset or unusable.
    """
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
    # filesystem-read-ok: explicit binary override validation needs executable-file metadata
    if shutil.which(str(resolved)) is None and not (
        # filesystem-read-ok: explicit binary override validation needs executable-file metadata
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
    """Return ``agent_config`` with its transport's binary override applied.

    Returns the SAME object when the transport has no override entry, the
    variable is unset, or the path fails validation -- so a caller can test
    identity to learn whether an override took effect.

    The override path is :func:`shlex.quote`\\ d so a path containing spaces
    survives the downstream :func:`shlex.split` as a single argv token.
    Operators needing extra flags on the wrapper should set
    ``[agents.<name>].cmd`` in their config instead.

    Args:
        agent_config: The resolved agent configuration for the smoke run.

    Returns:
        A copy with the overridden ``cmd``, or ``agent_config`` unchanged.
    """
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
    """Return a config copy with every overridden transport's agents rewritten.

    Covers the operator's ``[agents.<name>]`` entries -- including the
    fallback agents a chain names -- so a chain that falls through to another
    alias of the same transport still runs the override.

    Args:
        config: The loaded configuration.

    Returns:
        A copy whose ``agents`` honor every active override, or ``config``
        unchanged when no override applies.
    """
    new_agents: dict[str, AgentConfig] = {}
    changed = False
    for name, agent_config in config.agents.items():
        overridden = apply_smoke_binary_override(agent_config)
        changed = changed or overridden is not agent_config
        new_agents[name] = overridden
    if not changed:
        return config
    return config.model_copy(update={"agents": new_agents})
