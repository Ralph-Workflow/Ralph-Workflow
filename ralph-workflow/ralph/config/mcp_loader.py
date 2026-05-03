"""Three-layer mcp.toml loader.

Merge order (lowest → highest priority):
  1. Bundled default  - ralph/policy/defaults/mcp.toml   (ships in wheel)
  2. User-global      - $XDG_CONFIG_HOME/ralph-workflow-mcp.toml
                        (default: ~/.config/ralph-workflow-mcp.toml)
  3. Project-local    - .agent/mcp.toml  (resolved via WorkspaceScope)

Unlike the main config loader, TOML parse errors here are fail-fast: any
malformed file triggers SystemExit(1) rather than a silent empty-dict fallback.
"""

from __future__ import annotations

import tomllib
from os import getenv
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from pydantic import ValidationError

from ralph.config.loader import _deep_merge
from ralph.config.mcp_models import McpConfig

if TYPE_CHECKING:
    from ralph.workspace.scope import WorkspaceScope

_GLOBAL_MCP_FILENAME = "ralph-workflow-mcp.toml"
_LOCAL_MCP_FILENAME = "mcp.toml"


def _bundled_default_mcp_config_path() -> Path:
    return Path(__file__).parent.parent / "policy" / "defaults" / _LOCAL_MCP_FILENAME


def _global_mcp_config_path() -> Path:
    xdg = getenv("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / _GLOBAL_MCP_FILENAME
    return Path.home() / ".config" / _GLOBAL_MCP_FILENAME


def _local_mcp_config_path(workspace_scope: WorkspaceScope) -> Path:
    if hasattr(workspace_scope, "resolve_agent_file"):
        return workspace_scope.resolve_agent_file(_LOCAL_MCP_FILENAME)
    return workspace_scope.local_config_path.parent / _LOCAL_MCP_FILENAME


def _load_mcp_toml(path: Path) -> dict[str, object]:
    if not path.exists():
        logger.debug("MCP config not found, skipping: {}", path)
        return {}
    logger.debug("Loading MCP config from {}", path)
    with path.open("rb") as fh:
        try:
            data: dict[str, object] = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
            logger.error("MCP config parse error at {}: {}", path, exc)
            raise SystemExit(1) from exc
    return data


def _validate_fallback_backends(config: McpConfig) -> None:
    for entry in config.web_search.fallback:
        if entry != "ddgs" and entry not in config.web_search.backends:
            logger.error(
                "MCP config: fallback backend '{}' is not configured in"
                " [web_search.backends]; add a [web_search.backends.{}] section"
                " or remove it from the fallback list",
                entry,
                entry,
            )
            raise SystemExit(1)


def _inject_mcp_server_names(merged: dict[str, object]) -> None:
    raw_servers = merged.get("mcp_servers")
    if not isinstance(raw_servers, dict):
        return
    for server_name, server_spec in raw_servers.items():
        if isinstance(server_spec, dict) and "name" not in server_spec:
            server_spec["name"] = server_name


def load_mcp_config(
    workspace_scope: WorkspaceScope | None = None,
    config_path: Path | None = None,
) -> McpConfig:
    """Build merged McpConfig from all layers.

    Args:
        workspace_scope: Provides the project-local .agent/ root. Not used when
            config_path is given.
        config_path: Explicit override for the project-local layer.

    Returns:
        Validated McpConfig.

    Raises:
        SystemExit: On TOML parse error, schema validation failure, or unknown
            fallback backend reference.
    """
    bundled = _load_mcp_toml(_bundled_default_mcp_config_path())
    global_data = _load_mcp_toml(_global_mcp_config_path())

    if config_path is not None:
        local_data = _load_mcp_toml(config_path)
    elif workspace_scope is not None:
        local_data = _load_mcp_toml(_local_mcp_config_path(workspace_scope))
    else:
        local_data = {}

    merged = _deep_merge(bundled, global_data)
    merged = _deep_merge(merged, local_data)
    _inject_mcp_server_names(merged)

    try:
        config = McpConfig.model_validate(merged)
    except ValidationError as exc:
        logger.error("MCP config validation failed:\n{}", exc)
        raise SystemExit(1) from exc

    logger.debug(
        "MCP config loaded: {} server(s), web_search.enabled={}",
        len(config.mcp_servers),
        config.web_search.enabled,
    )
    _validate_fallback_backends(config)
    return config
