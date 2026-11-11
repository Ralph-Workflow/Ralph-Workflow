
from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from loguru import logger

from ralph.mcp.tools.names import (
    ALL_RALPH_TOOLS,
    OPENCODE_NATIVE_TOOLS_TO_KEEP,
    RALPH_MCP_SERVER_NAME,
    claude_tool_name,
)
from ralph.mcp.transport.common import merge_existing_upstreams
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers
from ralph.timeout_defaults import EXEC_MAX_TIMEOUT_MS, EXEC_SOFT_REPLY_BUDGET_MS

if TYPE_CHECKING:
    from collections.abc import Mapping

_OPENCODE_MCP_CLIENT_TIMEOUT_HEADROOM_MS: Final[int] = 30_000

_OPENCODE_MCP_CLIENT_TIMEOUT_MS: Final[int] = (
    max(EXEC_MAX_TIMEOUT_MS, EXEC_SOFT_REPLY_BUDGET_MS) + _OPENCODE_MCP_CLIENT_TIMEOUT_HEADROOM_MS
)

_GLOBAL_CONFIG_BASENAMES: Final = ("config.json", "opencode.json", "opencode.jsonc")

_DIRECTORY_CONFIG_BASENAMES: Final = ("opencode.json", "opencode.jsonc")

_OPENCODE_DIRNAME: Final = ".opencode"
_CONFIG_FILE_ENV: Final = "OPENCODE_CONFIG"
_CONFIG_DIR_ENV: Final = "OPENCODE_CONFIG_DIR"
_DISABLE_PROJECT_CONFIG_ENV: Final = "OPENCODE_DISABLE_PROJECT_CONFIG"
_XDG_CONFIG_HOME_ENV: Final = "XDG_CONFIG_HOME"
_TEST_HOME_ENV: Final = "OPENCODE_TEST_HOME"
_TEST_MANAGED_CONFIG_DIR_ENV: Final = "OPENCODE_TEST_MANAGED_CONFIG_DIR"
_PROGRAM_DATA_ENV: Final = "ProgramData"
_GIT_MARKER: Final = ".git"

_GATE_REPORT_MEMO: list[tuple[str, tuple[str, ...]] | None] = [None]


def merge_opencode_config_content(existing: str | None, endpoint: str) -> str:
    config_text, _upstreams = build_opencode_provider_config(existing, endpoint)
    return config_text


def build_opencode_provider_config(
    existing: str | None,
    endpoint: str,
    *,
    unsafe_mode: bool = False,
    workspace_path: Path | None = None,
) -> tuple[str, tuple[UpstreamMcpServer, ...]]:
    config_obj = _parse_opencode_config_content(existing)
    existing_mcp = config_obj.get("mcp")
    if isinstance(existing_mcp, dict):
        existing_for_upstreams = {
            name: entry
            for name, entry in cast("dict[str, object]", existing_mcp).items()
            if name != RALPH_MCP_SERVER_NAME
        }
        upstreams = normalize_upstream_mcp_servers(existing_for_upstreams)
    else:
        upstreams = ()

    report_opencode_mcp_servers_outside_ralph_gate(workspace_path)

    ralph_entry: dict[str, object] = {
        "type": "remote",
        "url": endpoint,
        "enabled": True,
        "timeout": _OPENCODE_MCP_CLIENT_TIMEOUT_MS,
    }
    current_config_mcp: dict[str, object] = (
        dict(cast("dict[str, object]", existing_mcp)) if isinstance(existing_mcp, dict) else {}
    )
    current_config_mcp["ralph"] = ralph_entry
    current_config: dict[str, object] = {"mcp": current_config_mcp}
    merged = merge_existing_upstreams("opencode", current_config, unsafe_mode=unsafe_mode)
    config_obj["mcp"] = merged.get("mcp", {"ralph": ralph_entry})

    experimental_obj = config_obj.setdefault("experimental", {})
    if not isinstance(experimental_obj, dict):
        experimental_obj = {}
        config_obj["experimental"] = experimental_obj
    cast("dict[str, object]", experimental_obj)["mcp_timeout"] = _OPENCODE_MCP_CLIENT_TIMEOUT_MS

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
    for native_name in OPENCODE_NATIVE_TOOLS_TO_KEEP:
        permission_section[native_name] = "allow"

    existing_tools = config_obj.get("tools", {})
    if not isinstance(existing_tools, dict):
        existing_tools = {}
    orchestration_overrides = dict.fromkeys(OPENCODE_NATIVE_TOOLS_TO_KEEP, True)
    config_obj["tools"] = {
        **cast("dict[str, object]", existing_tools),
        **orchestration_overrides,
    }

    config_obj.setdefault("$schema", "https://opencode.ai/config.json")
    return json.dumps(config_obj, sort_keys=True), upstreams


def load_existing_opencode_upstream_servers(
    workspace_path: Path | None = None,
) -> tuple[UpstreamMcpServer, ...]:
    raw_servers: dict[str, object] = {}
    for config_path in _opencode_config_paths(workspace_path):
        config = _read_config_object(config_path)
        if config is None:
            continue
        raw_servers.update(_mcp_servers_from_config(config, config_path))
    raw_servers.pop(RALPH_MCP_SERVER_NAME, None)

    normalized: dict[str, object] = {}
    for server_name, raw_entry in raw_servers.items():
        converted = _normalize_opencode_server_entry(server_name, raw_entry)
        if converted is not None:
            normalized[converted[0]] = converted[1]
    return normalize_upstream_mcp_servers(normalized)


def reset_opencode_mcp_gate_report() -> None:
    _GATE_REPORT_MEMO[0] = None


def report_opencode_mcp_servers_outside_ralph_gate(
    workspace_path: Path | None = None,
) -> tuple[str, ...]:
    memo_key = "" if workspace_path is None else str(workspace_path)
    memoized = _GATE_REPORT_MEMO[0]
    if memoized is not None and memoized[0] == memo_key:
        return memoized[1]
    discovered = tuple(
        server.name for server in load_existing_opencode_upstream_servers(workspace_path)
    )
    _GATE_REPORT_MEMO[0] = (memo_key, discovered)
    if discovered:
        logger.warning(
            "OpenCode has {} MCP server(s) from your own config that this run cannot "
            "gate: {}. OpenCode deep-merges its config sources, so Ralph Workflow's "
            "OPENCODE_CONFIG_CONTENT cannot replace them and does not try -- they keep "
            "working natively. Ralph Workflow does not re-expose them as "
            "ralph_upstream__* proxies either, because that would show the agent a "
            "second copy of every one of their tools; their calls therefore do not "
            "appear in Ralph Workflow's tool ledger. Run `opencode debug config` to "
            "see the resolved set.",
            len(discovered),
            ", ".join(discovered),
        )
    return discovered


def _opencode_config_paths(workspace_path: Path | None) -> tuple[Path, ...]:
    active_env: Mapping[str, str] = os.environ
    paths: list[Path] = []
    paths.extend(_global_config_dir(active_env) / name for name in _GLOBAL_CONFIG_BASENAMES)

    custom_config = active_env.get(_CONFIG_FILE_ENV)
    if custom_config:
        paths.append(Path(custom_config).expanduser())

    project_config_enabled = not _is_truthy(active_env.get(_DISABLE_PROJECT_CONFIG_ENV))
    if project_config_enabled:
        for directory in reversed(_project_directories(workspace_path)):
            paths.extend(directory / name for name in _DIRECTORY_CONFIG_BASENAMES)

    for directory in _dot_opencode_directories(
        workspace_path, active_env, project_config_enabled=project_config_enabled
    ):
        paths.extend(directory / name for name in _DIRECTORY_CONFIG_BASENAMES)

    paths.extend(_managed_config_dir(active_env) / name for name in _DIRECTORY_CONFIG_BASENAMES)
    return tuple(paths)


def _project_directories(workspace_path: Path | None) -> tuple[Path, ...]:
    if workspace_path is None:
        return ()
    walked: list[Path] = []
    for directory in (workspace_path, *workspace_path.parents):
        walked.append(directory)
        if _is_worktree_root(directory):
            return tuple(walked)
    return (workspace_path,)


def _dot_opencode_directories(
    workspace_path: Path | None,
    active_env: Mapping[str, str],
    *,
    project_config_enabled: bool,
) -> tuple[Path, ...]:
    config_dir_override = active_env.get(_CONFIG_DIR_ENV)
    candidates: list[Path] = [_global_config_dir(active_env)]
    if project_config_enabled:
        candidates.extend(
            directory / _OPENCODE_DIRNAME for directory in _project_directories(workspace_path)
        )
    candidates.append(_opencode_home(active_env) / _OPENCODE_DIRNAME)
    if config_dir_override:
        candidates.append(Path(config_dir_override).expanduser())

    selected: list[Path] = []
    for candidate in candidates:
        if candidate in selected:
            continue
        if candidate.name == _OPENCODE_DIRNAME or (
            config_dir_override is not None and str(candidate) == config_dir_override
        ):
            selected.append(candidate)
    return tuple(selected)


def _global_config_dir(active_env: Mapping[str, str]) -> Path:
    xdg_config_home = active_env.get(_XDG_CONFIG_HOME_ENV)
    base = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    return base / "opencode"


def _opencode_home(active_env: Mapping[str, str]) -> Path:
    test_home = active_env.get(_TEST_HOME_ENV)
    return Path(test_home) if test_home else Path.home()


def _managed_config_dir(active_env: Mapping[str, str]) -> Path:
    override = active_env.get(_TEST_MANAGED_CONFIG_DIR_ENV)
    if override:
        return Path(override)
    system_name = platform.system()
    if system_name == "Darwin":
        return Path("/Library/Application Support/opencode")
    if system_name == "Windows":
        return Path(active_env.get(_PROGRAM_DATA_ENV) or "C:\\ProgramData") / "opencode"
    return Path("/etc/opencode")


def _is_worktree_root(directory: Path) -> bool:
    return (directory / _GIT_MARKER).exists()


def _is_truthy(raw_value: str | None) -> bool:
    return raw_value is not None and raw_value.lower() in {"true", "1"}


def _read_config_object(config_path: Path) -> dict[str, object] | None:
    if not config_path.exists():
        return None
    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        logger.warning(
            "Cannot read OpenCode config {}: {}. Any MCP server it defines is "
            "missing from Ralph Workflow's view of this run, even though OpenCode "
            "itself may still load it.",
            config_path,
            error,
        )
        return None
    try:
        decoded: object = json.loads(_strip_jsonc(raw_text))
    except json.JSONDecodeError as error:
        logger.warning(
            "OpenCode config {} is not valid JSON: {}. Any MCP server it defines is "
            "missing from Ralph Workflow's view of this run.",
            config_path,
            error,
        )
        return None
    if not isinstance(decoded, dict):
        logger.warning(
            "OpenCode config {} is not a JSON object; ignoring it and any MCP server it defines.",
            config_path,
        )
        return None
    return cast("dict[str, object]", decoded)


def _mcp_servers_from_config(config: dict[str, object], config_path: Path) -> dict[str, object]:
    value = config.get("mcp")
    if value is None:
        return {}
    if not isinstance(value, dict):
        logger.warning(
            "OpenCode config {} has a non-object 'mcp' section; any server it meant "
            "to define is missing from Ralph Workflow's view of this run.",
            config_path,
        )
        return {}
    return dict(cast("dict[str, object]", value))


def _normalize_opencode_server_entry(
    server_name: str, raw_entry: object
) -> tuple[str, object] | None:
    if server_name == RALPH_MCP_SERVER_NAME or not isinstance(raw_entry, dict):
        return None
    entry = cast("dict[str, object]", raw_entry)
    if entry.get("enabled") is False or entry.get("disabled") is True:
        return None

    url = entry.get("url")
    if isinstance(url, str) and url:
        return server_name, {"url": url, "env": _server_environment(entry)}

    argv = _server_argv(entry)
    if argv:
        return server_name, {
            "command": argv[0],
            "args": argv[1:],
            "env": _server_environment(entry),
        }
    return None


def _server_argv(entry: dict[str, object]) -> list[str]:
    command = entry.get("command")
    if isinstance(command, str):
        raw_args = entry.get("args")
        args = cast("list[object]", raw_args) if isinstance(raw_args, list) else []
        return [command, *(arg for arg in args if isinstance(arg, str))] if command else []
    if isinstance(command, list):
        return [part for part in cast("list[object]", command) if isinstance(part, str)]
    return []


def _server_environment(entry: dict[str, object]) -> dict[str, str]:
    for key in ("environment", "env"):
        raw_environment = entry.get(key)
        if isinstance(raw_environment, dict):
            return {
                name: value
                for name, value in cast("dict[str, object]", raw_environment).items()
                if isinstance(value, str)
            }
    return {}


def _strip_jsonc(raw_text: str) -> str:
    out: list[str] = []
    in_string = False
    index = 0
    length = len(raw_text)
    while index < length:
        char = raw_text[index]
        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(raw_text[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if char == "/" and index + 1 < length and raw_text[index + 1] in {"/", "*"}:
            index = _end_of_comment(raw_text, index)
            continue
        if char in {"}", "]"}:
            _drop_trailing_comma(out)
        out.append(char)
        index += 1
    return "".join(out)


def _end_of_comment(raw_text: str, start: int) -> int:
    if raw_text[start + 1] == "/":
        end = raw_text.find("\n", start)
        return len(raw_text) if end == -1 else end
    end = raw_text.find("*/", start + 2)
    return len(raw_text) if end == -1 else end + 2


def _drop_trailing_comma(out: list[str]) -> None:
    index = len(out) - 1
    while index >= 0 and out[index].isspace():
        index -= 1
    if index >= 0 and out[index] == ",":
        del out[index]


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


__all__ = [
    "build_opencode_provider_config",
    "load_existing_opencode_upstream_servers",
    "merge_opencode_config_content",
    "report_opencode_mcp_servers_outside_ralph_gate",
    "reset_opencode_mcp_gate_report",
]
