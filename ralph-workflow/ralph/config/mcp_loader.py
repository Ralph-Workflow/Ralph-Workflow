from __future__ import annotations

import copy
from os import getenv
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from pydantic import ValidationError

from ralph.config._toml_fallback import load_optional_toml, validate_or_default
from ralph.config.config_error_messages import warn_unknown_top_level_fields
from ralph.config.loader import deep_merge as _deep_merge
from ralph.config.mcp_models import McpConfig
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.pydantic_validation_errors import format_validation_error_messages

if TYPE_CHECKING:
    from ralph.workspace.scope import WorkspaceScope

_GLOBAL_MCP_FILENAME = "ralph-workflow-mcp.toml"
_LOCAL_MCP_FILENAME = "mcp.toml"
_MCP_TOP_LEVEL_FIELDS = frozenset({"mcp_servers", "web_search", "web_visit", "media"})


class McpConfigError(SystemExit):
    def __init__(self, message: str, *, code: int = 1) -> None:
        super().__init__(code)
        self.message = message
        self.code = code

    def __str__(self) -> str:
        return self.message


def bundled_default_mcp_config_path() -> Path:
    return Path(__file__).parent.parent / "policy" / "defaults" / _LOCAL_MCP_FILENAME


def global_mcp_config_path() -> Path:
    xdg = getenv("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / _GLOBAL_MCP_FILENAME
    return Path.home() / ".config" / _GLOBAL_MCP_FILENAME


def local_mcp_config_path(workspace_scope: WorkspaceScope) -> Path:
    if hasattr(workspace_scope, "resolve_agent_file"):
        return workspace_scope.resolve_agent_file(_LOCAL_MCP_FILENAME)
    return workspace_scope.local_config_path.parent / _LOCAL_MCP_FILENAME


def _raise_mcp_validation_error(exc: ValidationError, path: Path) -> None:
    detail_lines = format_validation_error_messages(exc)
    detail_block = "\n".join(detail_lines) if detail_lines else "(no field detail available)"
    message = (
        "MCP config validation failed; the values below do not match the MCP config schema.\n"
        "WHY: a malformed MCP config means Ralph cannot start the servers / web-search "
        "backends you asked for, so it stops before launching anything.\n"
        f"FIX: fix the field-level errors in {path} (or "
        f"{global_mcp_config_path()} / {bundled_default_mcp_config_path()}, depending on "
        "where the bad value came from), then re-run `ralph --check-config`.\n"
        f"DETAILS:\n{detail_block}"
    )
    logger.error(message)
    raise McpConfigError(message) from exc


def _load_mcp_toml(
    path: Path,
    *,
    strict: bool = False,
    is_bundled: bool = False,
) -> dict[str, object]:
    path_obj = Path(path)
    if is_bundled and not DEFAULT_FILE_BACKEND.exists(path_obj):
        message = (
            f"Bundled default MCP config not found at {path_obj}.\n"
            "WHY: bundled package data is required for Ralph to operate.\n"
            "FIX: re-install ralph-workflow."
        )
        logger.error(message)
        raise McpConfigError(message)
    try:
        return load_optional_toml(path_obj, kind="mcp", strict=strict)
    except Exception as exc:
        message = (
            f"Could not parse MCP config at {path_obj}: {exc}.\n"
            "WHY: settings in a malformed TOML file are not safe to use, "
            "so Ralph refuses to guess what you meant and exits instead.\n"
            f"FIX: correct the TOML syntax in {path_obj}, then re-run "
            "`ralph --check-config`."
        )
        logger.error(message)
        raise McpConfigError(message) from exc


def _validate_fallback_backends(config: McpConfig) -> None:
    for entry in config.web_search.fallback:
        if entry != "ddgs" and entry not in config.web_search.backends:
            message = (
                f"MCP config: fallback backend '{entry}' is not configured in"
                f" [web_search.backends]; add a [web_search.backends.{entry}] section"
                " or remove it from the fallback list"
            )
            logger.error(message)
            raise McpConfigError(message)


def _validate_mcp_layer(
    path: Path,
    layer_data: dict[str, object],
    *,
    strict: bool,
) -> dict[str, object]:
    if not layer_data:
        return {}

    def _layer_validator(data: dict[str, object]) -> dict[str, object]:
        copied = copy.deepcopy(data)
        _inject_mcp_server_names(copied)
        try:
            cfg = McpConfig.model_validate(copied)
        except ValidationError as exc:
            if strict:
                _raise_mcp_validation_error(exc, path)
            raise
        _validate_fallback_backends(cfg)
        return data

    return validate_or_default(
        path,
        layer_data,
        validator=_layer_validator,
        default={},
        kind="mcp",
        strict=strict,
    )


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
    *,
    strict_optional_files: bool = False,
) -> McpConfig:
    bundled_path = bundled_default_mcp_config_path()
    global_path = global_mcp_config_path()
    if config_path is not None:
        local_path = config_path
    elif workspace_scope is not None:
        local_path = local_mcp_config_path(workspace_scope)
    else:
        local_path = Path(_LOCAL_MCP_FILENAME)

    bundled = _load_mcp_toml(bundled_path, strict=True, is_bundled=True)
    bundled = _validate_mcp_layer(bundled_path, bundled, strict=True)

    global_data = _load_mcp_toml(global_path, strict=strict_optional_files)
    global_data = _validate_mcp_layer(global_path, global_data, strict=strict_optional_files)

    if config_path is not None or workspace_scope is not None:
        local_data = _load_mcp_toml(local_path, strict=strict_optional_files)
        local_data = _validate_mcp_layer(local_path, local_data, strict=strict_optional_files)
    else:
        local_data = {}

    warn_unknown_top_level_fields(bundled, bundled_path, _MCP_TOP_LEVEL_FIELDS)
    warn_unknown_top_level_fields(global_data, global_path, _MCP_TOP_LEVEL_FIELDS)
    warn_unknown_top_level_fields(local_data, local_path, _MCP_TOP_LEVEL_FIELDS)

    merged = _deep_merge(bundled, global_data)
    merged = _deep_merge(merged, local_data)
    _inject_mcp_server_names(merged)

    try:
        config = McpConfig.model_validate(merged)
    except ValidationError as exc:
        _raise_mcp_validation_error(exc, local_path)

    logger.trace(
        "MCP config loaded: {} server(s), web_search.enabled={}",
        len(config.mcp_servers),
        config.web_search.enabled,
    )
    _validate_fallback_backends(config)
    return config
