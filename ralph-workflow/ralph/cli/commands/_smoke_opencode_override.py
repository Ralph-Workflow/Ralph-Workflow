"""``RALPH_OPENCODE_BINARY`` override helpers for the smoke CLI.

Extracted from :mod:`ralph.cli.commands.smoke` so the main smoke
module can stay under the 1000-line audit cap (see
:mod:`ralph.testing.audit_repo_structure`). The override parallels the
AGY and Cursor patterns already established in ``smoke.py`` -- the
extraction keeps the override's three seams (``_resolve_*``,
``_maybe_apply_*``, ``_apply_*_to_config``) intact and re-importable
by the smoke CLI's opencode harness command.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.config.agent_detection import opencode_binary_override

if TYPE_CHECKING:
    from ralph.config.models import AgentConfig, UnifiedConfig

__all__ = [
    "_apply_opencode_binary_override_to_config",
    "_maybe_apply_opencode_binary_override",
    "_resolve_opencode_binary_override",
]


def _resolve_opencode_binary_override() -> str | None:
    """Return the validated absolute ``RALPH_OPENCODE_BINARY`` override or ``None``.

    Mirrors :func:`ralph.cli.commands.smoke._resolve_agy_binary_override`
    and :func:`ralph.cli.commands.smoke._resolve_cursor_binary_override`
    for the OpenCode transport. A relative override is resolved
    against the current working directory so downstream
    :class:`subprocess.Popen` always sees an absolute path. The path
    must resolve to a regular file with executable bits set, or to a
    name ``shutil.which`` can locate on ``PATH``. When validation
    fails a WARNING is logged and ``None`` is returned so the caller
    falls back to the real ``opencode`` binary on ``PATH``.

    The override exists so a test-only deterministic stub can take
    the place of the real ``opencode`` binary without losing the
    MCP endpoint delivery the OpenCode runtime resolver wires into
    ``OPENCODE_CONFIG_CONTENT`` -- both the real binary and the
    stub read the same env var to discover Ralph's MCP server.
    """
    override = opencode_binary_override()
    if not override:
        return None
    resolved = Path(override).expanduser()
    if not resolved.is_absolute():
        resolved = resolved.resolve()
        logger.info(
            "Resolved relative RALPH_OPENCODE_BINARY '{}' to absolute path '{}'",
            override,
            resolved,
        )
    # filesystem-read-ok: explicit binary override validation needs executable-file metadata
    import shutil

    if shutil.which(str(resolved)) is None and not (
        # filesystem-read-ok: explicit binary override validation needs executable-file metadata
        resolved.is_file() and os.access(resolved, os.X_OK)
    ):
        logger.warning(
            "RALPH_OPENCODE_BINARY points to '{}', which is not executable; ignoring override",
            override,
        )
        return None
    return str(resolved)


def _maybe_apply_opencode_binary_override(
    agent_config: AgentConfig,
) -> AgentConfig:
    """Return a copy of ``agent_config`` that uses ``RALPH_OPENCODE_BINARY`` when set.

    Validates the override path (resolving relative paths to absolute)
    and leaves ``agent_config`` unchanged when the path is not
    executable or not a regular file, logging a WARNING in that case.
    The override path is shlex.quote()d when applied to the ``cmd``
    field so the wrapper path is preserved as a single argv token by
    downstream shlex.split. Operators that need extra flags on the
    wrapper can set ``[agents.opencode].cmd`` in their config (the
    OpenCode command builder honors that override via shlex.split on
    the config.cmd).
    """
    from ralph.config.enums import AgentTransport

    if agent_config.transport is not AgentTransport.OPENCODE:
        return agent_config
    resolved = _resolve_opencode_binary_override()
    if resolved is None:
        return agent_config
    logger.info("Using RALPH_OPENCODE_BINARY override: {}", resolved)
    return agent_config.model_copy(update={"cmd": shlex.quote(resolved)})


def _apply_opencode_binary_override_to_config(
    config: UnifiedConfig,
) -> UnifiedConfig:
    """Return a config copy with OpenCode agents using ``RALPH_OPENCODE_BINARY`` when set."""
    from ralph.config.enums import AgentTransport

    resolved = _resolve_opencode_binary_override()
    if resolved is None:
        return config
    quoted = shlex.quote(resolved)
    new_agents: dict[str, AgentConfig] = {}
    for name, agent_config in config.agents.items():
        if agent_config.transport is AgentTransport.OPENCODE:
            new_agents[name] = agent_config.model_copy(update={"cmd": quoted})
        else:
            new_agents[name] = agent_config
    return config.model_copy(update={"agents": new_agents})
