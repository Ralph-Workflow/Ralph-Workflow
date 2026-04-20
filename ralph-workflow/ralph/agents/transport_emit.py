"""Helpers that synthesize per-agent MCP transport wiring.

Ralph generates Claude/Codex/OpenCode-specific config payloads at agent
invocation time. Those emitters live here so they can be shared between
``ralph.agents.invoke`` and the upstream MCP probes in
``ralph.mcp.upstream.agent_probe`` without circular imports.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import tomllib
from pathlib import Path
from typing import cast

from loguru import logger

from ralph.config.mcp_loader import load_mcp_config
from ralph.mcp.tools.names import (
    ALL_RALPH_TOOLS,
    CODEX_NATIVE_FEATURES_TO_DISABLE,
    OPENCODE_NATIVE_TOOLS_TO_DISABLE,
    RALPH_MCP_SERVER_NAME,
    claude_tool_name,
)
from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_CONFIG_ENV,
    UpstreamMcpServer,
    normalize_upstream_mcp_servers,
    serialize_upstream_mcp_servers,
)


def _prepare_codex_home(
    endpoint: str | None,
    *,
    workspace_path: Path | None,
    existing_home: str | None,
    system_prompt_file: str | None,
) -> str:
    codex_home, _upstreams = _prepare_codex_home_with_upstreams(
        endpoint,
        workspace_path=workspace_path,
        existing_home=existing_home,
        system_prompt_file=system_prompt_file,
    )
    return codex_home


def _prepare_codex_home_with_upstreams(
    endpoint: str | None,
    *,
    workspace_path: Path | None,
    existing_home: str | None,
    system_prompt_file: str | None,
) -> tuple[str, tuple[UpstreamMcpServer, ...]]:
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
        base_config = _remove_all_toml_mcp_server_tables(base_config)
        features_in_base = "[features]" in base_config
        feature_lines = [
            f"{key.split('.', 1)[1]} = {value}"
            for key, value in CODEX_NATIVE_FEATURES_TO_DISABLE
            if "." in key
        ]
        feature_block = "\n".join(feature_lines) + "\n"
        prefix_sections.append('web_search = "disabled"\n')
        if features_in_base:
            base_config = base_config.replace("[features]\n", "[features]\n" + feature_block, 1)
        appended_sections.append(
            f'[mcp_servers.{RALPH_MCP_SERVER_NAME}]\nurl = "{endpoint}"\nenabled = true\n'
        )
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


def _claude_mcp_config(endpoint: str, *, workspace_path: Path | None = None) -> str:
    del workspace_path
    config_payload = {
        "mcpServers": {
            RALPH_MCP_SERVER_NAME: {
                "type": "http",
                "url": endpoint,
            }
        }
    }
    return json.dumps(config_payload, separators=(",", ":"))


def _load_existing_claude_upstream_servers(
    workspace_path: Path | None = None,
) -> tuple[UpstreamMcpServer, ...]:
    merged: dict[str, object] = {}
    for path in _claude_mcp_config_paths(workspace_path):
        config_obj = _parse_json_config_file(path)
        if not config_obj:
            continue
        value = config_obj.get("mcpServers")
        if isinstance(value, dict):
            merged = {**merged, **cast("dict[str, object]", value)}
    return normalize_upstream_mcp_servers(merged)


def _claude_mcp_config_paths(workspace_path: Path | None) -> tuple[Path, ...]:
    workspace_paths: tuple[Path, ...] = ()
    if workspace_path is not None:
        workspace_paths = (
            workspace_path / ".mcp.json",
            workspace_path / ".claude.json",
        )
    return (
        Path.home() / ".claude.json",
        *workspace_paths,
    )


def _parse_json_config_file(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        raw_payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw_payload, dict):
        return {}
    return cast("dict[str, object]", raw_payload)


def _merge_opencode_config_content(existing: str | None, endpoint: str) -> str:
    config_text, _upstreams = _build_opencode_provider_config(existing, endpoint)
    return config_text


def _build_opencode_provider_config(
    existing: str | None, endpoint: str
) -> tuple[str, tuple[UpstreamMcpServer, ...]]:
    config_obj = _parse_opencode_config_content(existing)
    existing_mcp = config_obj.get("mcp")
    upstreams = (
        normalize_upstream_mcp_servers(cast("dict[str, object]", existing_mcp))
        if isinstance(existing_mcp, dict)
        else ()
    )

    config_obj["mcp"] = {
        "ralph": {
            "type": "remote",
            "url": endpoint,
            "enabled": True,
            "timeout": 30000,
        }
    }

    permission_section_obj = config_obj.setdefault("permission", {})
    if not isinstance(permission_section_obj, dict):
        permission_section_obj = {}
        config_obj["permission"] = permission_section_obj
    permission_section = cast("dict[str, object]", permission_section_obj)
    permission_section["ralph_*"] = "allow"
    permission_section["mcp__ralph__*"] = "allow"
    for tool_name in ALL_RALPH_TOOLS:
        bare_name = str(tool_name)
        permission_section[bare_name] = "allow"
        permission_section[claude_tool_name(bare_name)] = "allow"

    existing_tools = config_obj.get("tools", {})
    if not isinstance(existing_tools, dict):
        existing_tools = {}
    disable_overrides = dict.fromkeys(OPENCODE_NATIVE_TOOLS_TO_DISABLE, False)
    config_obj["tools"] = {**cast("dict[str, object]", existing_tools), **disable_overrides}

    config_obj.setdefault("$schema", "https://opencode.ai/config.json")
    return json.dumps(config_obj, sort_keys=True), upstreams


def _parse_opencode_config_content(existing: str | None) -> dict[str, object]:
    if not existing:
        return {}
    try:
        decoded: object = json.loads(existing)
    except json.JSONDecodeError:
        return {}
    if not isinstance(decoded, dict):
        return {}
    return cast("dict[str, object]", decoded)


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


def _set_upstream_mcp_config(
    runtime_env: dict[str, str], upstreams: tuple[UpstreamMcpServer, ...]
) -> None:
    if upstreams:
        runtime_env[UPSTREAM_MCP_CONFIG_ENV] = serialize_upstream_mcp_servers(upstreams)
        return
    runtime_env.pop(UPSTREAM_MCP_CONFIG_ENV, None)


def _mcp_toml_as_upstreams(workspace_path: Path | None) -> tuple[UpstreamMcpServer, ...]:
    config_path = (workspace_path / ".agent" / "mcp.toml") if workspace_path is not None else None
    mcp_config = load_mcp_config(config_path=config_path)
    return tuple(
        UpstreamMcpServer(
            name=spec.name,
            transport=spec.transport,
            url=spec.url,
            command=spec.command,
            args=tuple(spec.args),
            env=dict(spec.env),
        )
        for spec in mcp_config.mcp_servers.values()
    )


def _merge_mcp_toml_into_upstreams(
    agent_native: tuple[UpstreamMcpServer, ...],
    mcp_toml_servers: tuple[UpstreamMcpServer, ...],
) -> tuple[UpstreamMcpServer, ...]:
    merged: dict[str, UpstreamMcpServer] = {s.name: s for s in agent_native}
    for server in mcp_toml_servers:
        if server.name in merged:
            logger.warning(
                "mcp.toml server '{}' overrides agent-native upstream config",
                server.name,
            )
        merged[server.name] = server
    return tuple(merged.values())


__all__ = [
    "_allocate_codex_home_dir",
    "_build_opencode_provider_config",
    "_claude_mcp_config",
    "_claude_mcp_config_paths",
    "_extract_codex_upstream_servers",
    "_load_existing_claude_upstream_servers",
    "_mcp_toml_as_upstreams",
    "_merge_mcp_toml_into_upstreams",
    "_merge_opencode_config_content",
    "_mirror_codex_home",
    "_parse_json_config_file",
    "_parse_opencode_config_content",
    "_prepare_codex_home",
    "_prepare_codex_home_with_upstreams",
    "_remove_all_toml_mcp_server_tables",
    "_remove_toml_table",
    "_set_upstream_mcp_config",
]
