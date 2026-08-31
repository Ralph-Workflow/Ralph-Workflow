"""OpenCode-specific MCP transport helpers."""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from loguru import logger

from ralph.mcp.tools.names import (
    ALL_RALPH_TOOLS,
    OPENCODE_NATIVE_TOOLS_TO_DISABLE,
    OPENCODE_NATIVE_TOOLS_TO_KEEP,
    RALPH_MCP_SERVER_NAME,
    claude_tool_name,
)
from ralph.mcp.transport.common import merge_existing_upstreams
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers
from ralph.timeout_defaults import EXEC_MAX_TIMEOUT_MS

if TYPE_CHECKING:
    from collections.abc import Mapping

#: OpenCode MCP client request timeout (ms). MUST exceed the longest possible
#: server-side tool execution — otherwise the client gives up with `-32001 Request
#: timed out` before the server finishes, producing a retry storm. exec is capped
#: at EXEC_MAX_TIMEOUT_MS (the largest any tool can run); add headroom for server
#: startup + output drain so even a max-length exec finishes before the client.
#:
#: IMPORTANT: OpenCode IGNORES the documented per-server ``mcp.<server>.timeout``
#: field and hard-enforces the MCP SDK default (~60s). The setting it actually
#: honors is the global ``experimental.mcp_timeout`` (opencode issues #8701/#8121).
#: We set BOTH: the experimental key is the one that takes effect; the per-server
#: field is kept for forward-compat if/when opencode starts honoring it.
_OPENCODE_MCP_CLIENT_TIMEOUT_MS = EXEC_MAX_TIMEOUT_MS + 30_000

#: Config file names OpenCode reads out of its GLOBAL config directory, in the
#: order it merges them (later wins). ``config.json`` is the pre-rename spelling
#: and is still loaded first.
_GLOBAL_CONFIG_BASENAMES: Final = ("config.json", "opencode.json", "opencode.jsonc")

#: Config file names OpenCode reads out of every OTHER config directory, in the
#: order it merges them (later wins).
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

#: One-slot memo of ``(workspace key, discovered server names)``. A run drives
#: one workspace, so one slot turns the out-of-gate notice into a once-per-run
#: operator notice instead of a config re-read on every agent cycle, and it
#: cannot grow. ``functools.lru_cache`` would say the same thing more briefly
#: but erases the signature to ``Any``, which mypy rejects under
#: ``disallow_any_expr``.
_GATE_REPORT_MEMO: list[tuple[str, tuple[str, ...]] | None] = [None]


def merge_opencode_config_content(existing: str | None, endpoint: str) -> str:
    """Merge Ralph MCP endpoint into an existing OpenCode config and return JSON."""
    config_text, _upstreams = build_opencode_provider_config(existing, endpoint)
    return config_text


def build_opencode_provider_config(
    existing: str | None,
    endpoint: str,
    *,
    unsafe_mode: bool = False,
    workspace_path: Path | None = None,
) -> tuple[str, tuple[UpstreamMcpServer, ...]]:
    """Build a full OpenCode config JSON with Ralph MCP and return it with upstream servers.

    Ralph's own entry is dropped before normalization in BOTH modes. ``ralph``
    is a reserved upstream name and ``normalize_upstream_mcp_servers`` raises
    on it, so an operator config that already names it -- one written by hand,
    or copied back from a config Ralph synthesized -- used to abort every
    restricted-mode OpenCode invocation instead of being replaced by the live
    endpoint.

    The returned upstream tuple carries ONLY the servers that came in through
    ``existing`` (the ``OPENCODE_CONFIG_CONTENT`` payload Ralph is about to
    overwrite). The servers the operator declared in their own OpenCode config
    FILES are discovered too -- see
    :func:`report_opencode_mcp_servers_outside_ralph_gate` -- but deliberately
    are neither proxied nor restated here; see that function for why.
    """
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

    # Discovery runs in BOTH modes and does not depend on OPENCODE_CONFIG_CONTENT
    # being set: the operator's servers live in files OpenCode loads on its own.
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

    # The field OpenCode actually honors for the MCP request timeout (the per-server
    # `timeout` above is ignored). Without this, long tool calls (exec running tests/
    # builds, large reads) die at OpenCode's ~60s default with `-32001`.
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
    # Native orchestration tools (sub-agents, skills, todos, web) stay enabled and
    # must be auto-allowed so they cannot wedge a headless run on an approval prompt.
    for native_name in OPENCODE_NATIVE_TOOLS_TO_KEEP:
        permission_section[native_name] = "allow"

    existing_tools = config_obj.get("tools", {})
    if not isinstance(existing_tools, dict):
        existing_tools = {}
    disable_overrides = dict.fromkeys(OPENCODE_NATIVE_TOOLS_TO_DISABLE, False)
    config_obj["tools"] = {**cast("dict[str, object]", existing_tools), **disable_overrides}

    config_obj.setdefault("$schema", "https://opencode.ai/config.json")
    return json.dumps(config_obj, sort_keys=True), upstreams


def load_existing_opencode_upstream_servers(
    workspace_path: Path | None = None,
) -> tuple[UpstreamMcpServer, ...]:
    """Read every place OpenCode takes MCP servers from and return them as upstreams.

    The source list mirrors ``Config.loadInstanceState`` in the OpenCode 1.18.25
    binary, lowest precedence first, and each entry was confirmed against
    ``opencode debug config``:

    1. the global config dir (``$XDG_CONFIG_HOME/opencode``, else
       ``~/.config/opencode``): ``config.json``, ``opencode.json``,
       ``opencode.jsonc``
    2. ``$OPENCODE_CONFIG`` -- an explicit config FILE
    3. project ``opencode.json`` / ``opencode.jsonc`` for every directory from
       the git worktree root down to the workspace
    4. ``<dir>/.opencode/opencode.json``/``.jsonc`` for the workspace and its
       ancestors, then ``~/.opencode``, then ``$OPENCODE_CONFIG_DIR``
    5. the managed config dir (``/Library/Application Support/opencode`` on
       macOS, ``%ProgramData%/opencode`` on Windows, ``/etc/opencode``
       elsewhere)

    ``$OPENCODE_DISABLE_PROJECT_CONFIG`` suppresses BOTH project sources (3 and
    the project half of 4), exactly as it does in the binary.

    Not read: the ``.well-known`` and console-account configs (network sources),
    the managed-preferences profile (needs a ``plutil`` subprocess), and
    ``OPENCODE_CONFIG_CONTENT`` -- Ralph writes that one itself and passes it
    in as ``existing``.

    Ralph's own entry is dropped first. ``ralph`` is a reserved upstream name
    and ``normalize_upstream_mcp_servers`` raises on it, so an operator config
    that already names it -- one written by hand, or copied back from a config
    Ralph synthesized -- would otherwise abort the run instead of being
    replaced by the live endpoint.
    """
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
    """Forget the memo so the next report re-reads the operator's OpenCode config."""
    _GATE_REPORT_MEMO[0] = None


def report_opencode_mcp_servers_outside_ralph_gate(
    workspace_path: Path | None = None,
) -> tuple[str, ...]:
    """Name the operator's OpenCode MCP servers that this run cannot gate.

    OpenCode is the one supported transport Ralph does not run with a
    "this config and nothing else" flag. ``Config.loadInstanceState``
    DEEP-MERGES its sources, so the ``mcp`` map Ralph publishes through
    ``OPENCODE_CONFIG_CONTENT`` is folded into -- never substituted for -- the
    operator's own ``opencode.json``. Their servers reach the agent in
    restricted mode and in unsafe mode alike, and that is the intended
    outcome: Ralph must never disable what the operator installed.

    Which is exactly why those servers are NOT returned from
    :func:`build_opencode_provider_config` as ``ralph_upstream__*`` proxies.
    The proxy contract exists to give back capabilities a strict provider
    config takes away (Claude's ``--strict-mcp-config``, Codex's synthesized
    home). Here nothing is taken away, so a proxy would be a SECOND copy of
    every tool the agent can already call: the tool list doubles, Ralph spawns
    its own duplicate stdio child for every ``type: "local"`` server the agent
    is already running, and the gate buys no enforcement because the ungated
    native name still works. Proxying a remote server would be worse than
    useless -- :class:`~ralph.mcp.upstream.config.UpstreamMcpServer` carries no
    ``headers``, so an authenticated upstream would appear beside its working
    native twin as a duplicate that cannot connect.

    So Ralph learns about them and SAYS so, rather than shadowing them. The
    warning is emitted once per run; call :func:`reset_opencode_mcp_gate_report`
    to force a fresh read.
    """
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
    """Return every OpenCode config file path, lowest merge precedence first."""
    active_env: Mapping[str, str] = os.environ
    paths: list[Path] = []
    paths.extend(_global_config_dir(active_env) / name for name in _GLOBAL_CONFIG_BASENAMES)

    custom_config = active_env.get(_CONFIG_FILE_ENV)
    if custom_config:
        paths.append(Path(custom_config).expanduser())

    project_config_enabled = not _is_truthy(active_env.get(_DISABLE_PROJECT_CONFIG_ENV))
    if project_config_enabled:
        # OpenCode reverses its walk-up for project files, so the outermost
        # directory merges first and the workspace's own file wins.
        for directory in reversed(_project_directories(workspace_path)):
            paths.extend(directory / name for name in _DIRECTORY_CONFIG_BASENAMES)

    for directory in _dot_opencode_directories(
        workspace_path, active_env, project_config_enabled=project_config_enabled
    ):
        paths.extend(directory / name for name in _DIRECTORY_CONFIG_BASENAMES)

    paths.extend(_managed_config_dir(active_env) / name for name in _DIRECTORY_CONFIG_BASENAMES)
    return tuple(paths)


def _project_directories(workspace_path: Path | None) -> tuple[Path, ...]:
    """Return the workspace and its ancestors up to the git worktree root.

    OpenCode walks from its working directory to the worktree root. When there
    is no git marker above the workspace the walk stops at the workspace itself
    rather than reading unrelated configs from every ancestor up to ``/``.
    """
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
    """Return the ``.opencode`` directories OpenCode loads, in merge order.

    OpenCode does NOT reverse this walk, so a parent's ``.opencode`` merges
    after -- and therefore wins over -- the workspace's own. That is faithfully
    mirrored here: guessing the more intuitive order would make Ralph report a
    different server than the one the agent actually gets.
    """
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
        # The global config dir is in OpenCode's directory list but is filtered
        # back out unless it IS the OPENCODE_CONFIG_DIR override; its files were
        # already merged as the global config above.
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
    """Return the home directory OpenCode resolves ``~/.opencode`` against."""
    test_home = active_env.get(_TEST_HOME_ENV)
    return Path(test_home) if test_home else Path.home()


def _managed_config_dir(active_env: Mapping[str, str]) -> Path:
    """Return the machine-managed OpenCode config directory for this platform."""
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
    """Return whether OpenCode would read this env value as enabled."""
    return raw_value is not None and raw_value.lower() in {"true", "1"}


def _read_config_object(config_path: Path) -> dict[str, object] | None:
    """Parse an OpenCode config file, warning rather than failing silently."""
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
            "OpenCode config {} is not a JSON object; ignoring it and any MCP "
            "server it defines.",
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
    """Translate one OpenCode ``mcp`` entry into Ralph's upstream server shape.

    OpenCode spells a stdio server as ``{"type": "local", "command": [exe, ...args]}``
    with its environment under ``environment``. Ralph's normalizer wants a string
    ``command`` beside a separate ``args`` list, so an untranslated entry was
    dropped outright -- silently losing every local server the operator ran.
    """
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
    """Return an OpenCode stdio entry's argv, whichever spelling it uses."""
    command = entry.get("command")
    if isinstance(command, str):
        raw_args = entry.get("args")
        args = cast("list[object]", raw_args) if isinstance(raw_args, list) else []
        return [command, *(arg for arg in args if isinstance(arg, str))] if command else []
    if isinstance(command, list):
        return [part for part in cast("list[object]", command) if isinstance(part, str)]
    return []


def _server_environment(entry: dict[str, object]) -> dict[str, str]:
    """Return an OpenCode server entry's environment (``environment``, else ``env``)."""
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
    """Return ``raw_text`` with JSONC comments and trailing commas removed.

    ``opencode.jsonc`` is a first-class config name and OpenCode parses it with
    comments and trailing commas allowed, so a strict :func:`json.loads` would
    read an operator's real config as unparseable and report no servers.
    """
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
