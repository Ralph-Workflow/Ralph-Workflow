"""Codex-specific MCP transport helpers."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import tomllib
from pathlib import Path
from typing import cast

from loguru import logger

from ralph.mcp.tools.names import (
    CODEX_NATIVE_FEATURE_OVERRIDES,
    RALPH_MCP_SERVER_NAME,
)
from ralph.mcp.transport.common import merge_existing_upstreams
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers


def prepare_codex_home(
    endpoint: str | None,
    *,
    workspace_path: Path | None,
    existing_home: str | None,
    system_prompt_file: str | None,
    unsafe_mode: bool = False,
) -> str:
    """Prepare an isolated Codex home directory and return its path."""
    codex_home, _upstreams = prepare_codex_home_with_upstreams(
        endpoint,
        workspace_path=workspace_path,
        existing_home=existing_home,
        system_prompt_file=system_prompt_file,
        unsafe_mode=unsafe_mode,
    )
    return codex_home


def _flat_dict_to_toml_servers(flat_dict: dict[str, object]) -> str:
    """Convert a flat dict with mcp_servers.X keys to TOML server sections."""
    lines: list[str] = []
    for key, value in sorted(flat_dict.items()):
        if not isinstance(key, str) or not key.startswith("mcp_servers."):
            continue
        lines.append(f"[{key}]")
        if isinstance(value, dict):
            for k, v in sorted(value.items()):
                lines.append(f"{k} = {json.dumps(v)}")
    return "\n".join(lines)


def prepare_codex_home_with_upstreams(
    endpoint: str | None,
    *,
    workspace_path: Path | None,
    existing_home: str | None,
    system_prompt_file: str | None,
    unsafe_mode: bool = False,
) -> tuple[str, tuple[UpstreamMcpServer, ...]]:
    """Prepare an isolated Codex home directory and return its path with upstream servers."""
    codex_root = _allocate_codex_home_dir(workspace_path)
    codex_root.mkdir(parents=True, exist_ok=True)

    source_home = Path(existing_home).expanduser() if existing_home else Path.home() / ".codex"
    if source_home.exists():
        _mirror_codex_home(source_home, codex_root)
    source_config = source_home / "config.toml"
    base_config = source_config.read_text(encoding="utf-8") if source_config.exists() else ""
    upstreams = _extract_codex_upstream_servers(base_config)
    prefix_sections: list[str] = []
    appended_sections: list[str] = []
    if endpoint:
        logger.warning(
            "Codex MCP tool restriction is best-effort: apply_patch and core "
            "editing primitives cannot be disabled. See "
            "ralph-workflow/docs/mcp-tool-restriction.md."
        )
        existing_from_base: dict[str, object] = {}
        if base_config.strip():
            try:
                parsed: object = tomllib.loads(base_config)
                if isinstance(parsed, dict):
                    existing_from_base = {
                        key: value
                        for key, value in parsed.items()
                        if isinstance(key, str) and key.startswith("mcp_servers.")
                    }
            except Exception:
                pass
        if not unsafe_mode:
            base_config = _remove_all_toml_mcp_server_tables(base_config)
        merged = merge_existing_upstreams(
            "codex",
            existing_from_base,
            unsafe_mode=unsafe_mode,
        )
        merged_toml = _flat_dict_to_toml_servers(merged)
        if merged_toml:
            appended_sections.append(merged_toml + "\n")
        ralph_section = (
            f'[mcp_servers.{RALPH_MCP_SERVER_NAME}]\nurl = "{endpoint}"\nenabled = true\n'
        )
        if ralph_section.strip() not in merged_toml:
            appended_sections.append(ralph_section)
        features_in_base = "[features]" in base_config
        feature_lines = [
            f"{key.split('.', 1)[1]} = {value}" for key, value in CODEX_NATIVE_FEATURE_OVERRIDES
        ]
        feature_block = "\n".join(feature_lines) + "\n"
        if features_in_base:
            base_config = base_config.replace("[features]\n", "[features]\n" + feature_block, 1)
        if not features_in_base:
            appended_sections.append("[features]\n" + feature_block)
    if system_prompt_file:
        prefix_sections.append(f"model_instructions_file = {json.dumps(system_prompt_file)}\n")
    config_suffix = "\n".join(section.rstrip() for section in appended_sections if section.strip())
    prefix_text = "\n".join(section.rstrip() for section in prefix_sections if section.strip())
    config_text = "\n\n".join(
        part for part in [prefix_text, base_config.rstrip(), config_suffix] if part
    )
    (codex_root / "config.toml").write_text(config_text, encoding="utf-8")
    return str(codex_root), upstreams


def _remove_toml_table(config_text: str, table_name: str) -> str:
    pattern = re.compile(
        rf"(?ms)^\[{re.escape(table_name)}\]\n.*?(?=^\[|\Z)",
    )
    return pattern.sub("", config_text).strip()


def _remove_all_toml_mcp_server_tables(config_text: str) -> str:
    pattern = re.compile(r"(?ms)^\[mcp_servers(?:\.[^\]]+)?\]\n.*?(?=^\[|\Z)")
    return pattern.sub("", config_text).strip()


def _mirror_codex_home(source_home: Path, codex_root: Path) -> None:
    for entry in source_home.iterdir():
        if entry.name == "config.toml":
            continue
        destination = codex_root / entry.name
        try:
            destination.symlink_to(entry, target_is_directory=entry.is_dir())
        except OSError:
            if entry.is_dir():
                shutil.copytree(entry, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(entry, destination)


def _allocate_codex_home_dir(workspace_path: Path | None) -> Path:
    if workspace_path is None:
        return Path(tempfile.mkdtemp(prefix="ralph-codex-home-"))

    tmp_root = workspace_path / ".agent" / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="codex-home-", dir=str(tmp_root)))


def _extract_codex_upstream_servers(config_text: str) -> tuple[UpstreamMcpServer, ...]:
    if not config_text.strip():
        return ()
    try:
        parsed: object = tomllib.loads(config_text)
    except Exception:
        return ()
    if not isinstance(parsed, dict):
        return ()
    mcp_servers = parsed.get("mcp_servers")
    if not isinstance(mcp_servers, dict):
        return ()
    return normalize_upstream_mcp_servers(cast("dict[str, object]", mcp_servers))


__all__ = [
    "prepare_codex_home",
    "prepare_codex_home_with_upstreams",
]
