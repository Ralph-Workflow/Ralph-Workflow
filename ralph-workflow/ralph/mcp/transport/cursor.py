from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Final, cast

from loguru import logger

from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.transport.common import _load_mcpservers_from_paths
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers


def _cursor_global_config_path() -> Path:
    return Path.home() / ".cursor" / "mcp.json"


def _cursor_workspace_config_path(workspace_path: Path) -> Path:
    return workspace_path / ".cursor" / "mcp.json"


def cursor_mcp_config(endpoint: str) -> str:
    config_payload = {
        "mcpServers": {
            RALPH_MCP_SERVER_NAME: {
                "url": endpoint,
            }
        }
    }
    return json.dumps(config_payload, separators=(",", ":"))


def _cursor_paths_to_consider(
    workspace_path: Path | None,
) -> tuple[Path, ...]:
    workspace_paths: tuple[Path, ...] = ()
    if workspace_path is not None:
        workspace_paths = (_cursor_workspace_config_path(workspace_path),)
    return (
        *workspace_paths,
        _cursor_global_config_path(),
    )


def _normalize_cursor_server_entry(name: str, entry: object) -> tuple[str, object] | None:
    if name == RALPH_MCP_SERVER_NAME:
        return None
    if not isinstance(entry, Mapping):
        return None
    return name, cast("dict[str, object]", entry)


_MIRROR_COPY_BYTE_BUDGET: Final[int] = 64 * 1024 * 1024


def _exceeds_copy_budget(entry: Path, budget: int) -> bool:
    if entry.is_file():
        return entry.stat().st_size > budget
    total = 0
    for child in entry.rglob("*"):
        if not child.is_file() or child.is_symlink():
            continue
        total += child.stat().st_size
        if total > budget:
            return True
    return False


def _mirror_cursor_home(source_cursor_root: Path, private_cursor_root: Path) -> None:
    if not source_cursor_root.exists():
        return
    for entry in source_cursor_root.iterdir():
        if entry.name == "mcp.json" or entry.name.endswith(".ralph.lock"):
            continue
        destination = private_cursor_root / entry.name
        try:
            target_is_directory = entry.is_dir()
            destination.symlink_to(entry, target_is_directory=target_is_directory)
        except FileNotFoundError:
            continue
        except OSError:
            try:
                _copy_cursor_home_entry(entry, destination)
            except FileNotFoundError:
                continue


def _copy_cursor_home_entry(entry: Path, destination: Path) -> None:
    if _exceeds_copy_budget(entry, _MIRROR_COPY_BYTE_BUDGET):
        logger.warning(
            "Cursor home entry {} could not be symlinked and is larger than {} bytes; "
            "leaving it out of this run's private HOME rather than copying it per invocation.",
            entry,
            _MIRROR_COPY_BYTE_BUDGET,
        )
        return
    if entry.is_dir():
        shutil.copytree(entry, destination, dirs_exist_ok=True)
    else:
        shutil.copy2(entry, destination)


def load_existing_cursor_upstream_servers(
    workspace_path: Path | None = None,
) -> tuple[UpstreamMcpServer, ...]:
    return normalize_upstream_mcp_servers(
        _load_mcpservers_from_paths(
            _cursor_paths_to_consider(workspace_path),
            _normalize_cursor_server_entry,
        )
    )


__all__ = [
    "_mirror_cursor_home",
    "cursor_mcp_config",
    "load_existing_cursor_upstream_servers",
]
