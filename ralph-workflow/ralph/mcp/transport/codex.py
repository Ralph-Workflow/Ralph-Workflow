from __future__ import annotations

import atexit
import collections
import math
import shutil
import tempfile
import tomllib
from pathlib import Path
from typing import Final, cast

import tomli_w
from loguru import logger

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.mcp.tools.names import (
    CODEX_NATIVE_FEATURE_OVERRIDES,
    RALPH_MCP_SERVER_NAME,
)
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers
from ralph.timeout_defaults import EXEC_SOFT_REPLY_BUDGET_MS
from ralph.workspace.agent_dir_retention import (
    register_temporary_path_owner,
    unregister_temporary_path_owner,
)

_CODEX_TOOL_TIMEOUT_HEADROOM_SECONDS: Final[int] = 30

_CODEX_TOOL_TIMEOUT_SEC: Final[int] = math.ceil(
    (EXEC_SOFT_REPLY_BUDGET_MS + _CODEX_TOOL_TIMEOUT_HEADROOM_SECONDS * 1000) / 1000.0
)

_DEFAULT_CODEX_HOME_CAP: int = 64
_ALL_CODEX_HOMES_CAP: Final[int] = 1024

_allocated_codex_homes: collections.deque[str] = collections.deque(maxlen=_DEFAULT_CODEX_HOME_CAP)
_all_allocated_codex_homes: set[str] = set()


def cleanup_codex_homes() -> None:
    for home in list(_all_allocated_codex_homes):
        shutil.rmtree(home, ignore_errors=True)
    _all_allocated_codex_homes.clear()
    _allocated_codex_homes.clear()


def release_codex_home(codex_home: str) -> bool:
    home_path = Path(codex_home)
    workspace_root = _workspace_root_for_codex_home(home_path)
    if workspace_root is not None:
        unregister_temporary_path_owner(workspace_root, home_path)
    _all_allocated_codex_homes.discard(codex_home)
    try:
        _allocated_codex_homes.remove(codex_home)
    except ValueError:
        shutil.rmtree(codex_home, ignore_errors=True)
        return False
    shutil.rmtree(codex_home, ignore_errors=True)
    return True


atexit.register(cleanup_codex_homes)


def _workspace_root_for_codex_home(path: Path) -> Path | None:
    try:
        if path.parent.name == "tmp" and path.parent.parent.name == ".agent":
            return path.parent.parent.parent
    except OSError:
        return None
    return None


def prepare_codex_home(
    endpoint: str | None,
    *,
    workspace_path: Path | None,
    existing_home: str | None,
    master_prompt_file: str | None,
    unsafe_mode: bool = False,
    run_id: str | None = None,
) -> str:
    codex_home, _upstreams = prepare_codex_home_with_upstreams(
        endpoint,
        workspace_path=workspace_path,
        existing_home=existing_home,
        master_prompt_file=master_prompt_file,
        unsafe_mode=unsafe_mode,
        run_id=run_id,
    )
    return codex_home


def _mapping_at(config: dict[str, object], key: str) -> dict[str, object]:
    value = config.get(key)
    if isinstance(value, dict):
        return cast("dict[str, object]", value)
    return {}


def _ralph_feature_overrides() -> dict[str, object]:
    return {key.split(".", 1)[1]: value == "true" for key, value in CODEX_NATIVE_FEATURE_OVERRIDES}


def _merge_codex_config(
    base: dict[str, object],
    *,
    endpoint: str | None,
    master_prompt_file: str | None,
    unsafe_mode: bool,
) -> dict[str, object]:
    merged = dict(base)
    if endpoint:
        merged["features"] = {**_mapping_at(base, "features"), **_ralph_feature_overrides()}
        merged["agents"] = {**_mapping_at(base, "agents"), "enabled": True}
        operator_servers = _mapping_at(base, "mcp_servers") if unsafe_mode else {}
        merged["mcp_servers"] = {
            **{
                name: value
                for name, value in operator_servers.items()
                if name != RALPH_MCP_SERVER_NAME
            },
            RALPH_MCP_SERVER_NAME: {
                "url": endpoint,
                "enabled": True,
                "tool_timeout_sec": _CODEX_TOOL_TIMEOUT_SEC,
            },
        }
    if master_prompt_file:
        merged["model_instructions_file"] = master_prompt_file
    return merged


class CodexConfigError(RuntimeError):
    pass


def _parse_source_config(base_config: str, source_config: Path) -> dict[str, object]:
    if not base_config.strip():
        return {}
    try:
        parsed: object = tomllib.loads(base_config)
    except ValueError as exc:
        msg = (
            f"Source Codex config {source_config} is not loadable TOML ({exc}). "
            "Ralph will not run Codex against settings the operator never configured "
            "-- fix that file, or move it aside to run with Codex's own defaults."
        )
        raise CodexConfigError(msg) from exc
    if not isinstance(parsed, dict):
        return {}
    return cast("dict[str, object]", parsed)


def _same_toml_value(left: object, right: object) -> bool:
    if isinstance(left, float) and isinstance(right, float):
        return left == right or (math.isnan(left) and math.isnan(right))
    if isinstance(left, dict) and isinstance(right, dict):
        left_map = cast("dict[str, object]", left)
        right_map = cast("dict[str, object]", right)
        return left_map.keys() == right_map.keys() and all(
            _same_toml_value(value, right_map[key]) for key, value in left_map.items()
        )
    if isinstance(left, list) and isinstance(right, list):
        left_seq = cast("list[object]", left)
        right_seq = cast("list[object]", right)
        return len(left_seq) == len(right_seq) and all(
            _same_toml_value(one, other) for one, other in zip(left_seq, right_seq, strict=True)
        )
    return left == right


def _render_codex_config(merged: dict[str, object], config_path: Path) -> str:
    config_text = tomli_w.dumps(merged)
    try:
        reparsed: object = tomllib.loads(config_text)
    except ValueError as exc:
        logger.error(
            "Synthesized Codex config {} did not round-trip ({}); Codex will refuse to start.",
            config_path,
            exc,
        )
        return config_text
    if not _same_toml_value(reparsed, merged):
        logger.error(
            "Synthesized Codex config {} does not read back as the merged settings; "
            "Codex may behave differently from what Ralph configured.",
            config_path,
        )
    return config_text


def synthesize_codex_config(
    base_config: str,
    *,
    source_config: Path,
    config_path: Path,
    endpoint: str | None,
    master_prompt_file: str | None,
    unsafe_mode: bool,
) -> tuple[str, tuple[UpstreamMcpServer, ...]]:
    base = _parse_source_config(base_config, source_config)
    upstreams = _extract_codex_upstream_servers(base)
    merged = _merge_codex_config(
        base,
        endpoint=endpoint,
        master_prompt_file=master_prompt_file,
        unsafe_mode=unsafe_mode,
    )
    return _render_codex_config(merged, config_path), upstreams


_synthesize_codex_config = synthesize_codex_config


def prepare_codex_home_with_upstreams(
    endpoint: str | None,
    *,
    workspace_path: Path | None,
    existing_home: str | None,
    master_prompt_file: str | None,
    unsafe_mode: bool = False,
    run_id: str | None = None,
) -> tuple[str, tuple[UpstreamMcpServer, ...]]:
    codex_root = _allocate_codex_home_dir(workspace_path)
    if workspace_path is not None and run_id is not None:
        register_temporary_path_owner(workspace_path, codex_root, run_id)
    codex_root.mkdir(parents=True, exist_ok=True)

    source_home = Path(existing_home).expanduser() if existing_home else Path.home() / ".codex"
    if source_home.exists():
        _mirror_codex_home(source_home, codex_root)
    source_config = source_home / "config.toml"
    base_config = source_config.read_text(encoding="utf-8") if source_config.exists() else ""
    config_path = codex_root / "config.toml"
    try:
        config_text, upstreams = _synthesize_codex_config(
            base_config,
            source_config=source_config,
            config_path=config_path,
            endpoint=endpoint,
            master_prompt_file=master_prompt_file,
            unsafe_mode=unsafe_mode,
        )
    except RecursionError as exc:
        msg = (
            f"Source Codex config {source_config} nests tables too deeply for Ralph "
            "to re-serialize. Ralph will not run Codex against settings the operator "
            "never configured -- flatten that file to run with it."
        )
        raise CodexConfigError(msg) from exc
    write_text_if_changed(DEFAULT_FILE_BACKEND, config_path, config_text, encoding="utf-8")
    return str(codex_root), upstreams


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


def _mirror_codex_home(source_home: Path, codex_root: Path) -> None:
    for entry in source_home.iterdir():
        if entry.name == "config.toml":
            continue
        destination = codex_root / entry.name
        try:
            destination.symlink_to(entry, target_is_directory=entry.is_dir())
        except OSError:
            _copy_codex_home_entry(entry, destination)


def _copy_codex_home_entry(entry: Path, destination: Path) -> None:
    if _exceeds_copy_budget(entry, _MIRROR_COPY_BYTE_BUDGET):
        logger.warning(
            "Codex home entry {} could not be symlinked and is larger than {} bytes; "
            "leaving it out of this run's CODEX_HOME rather than copying it per "
            "invocation. Codex recreates its own session state; operator settings, "
            "auth and plugins are unaffected.",
            entry,
            _MIRROR_COPY_BYTE_BUDGET,
        )
        return
    if entry.is_dir():
        shutil.copytree(entry, destination, dirs_exist_ok=True)
    else:
        shutil.copy2(entry, destination)


def _allocate_codex_home_dir(workspace_path: Path | None) -> Path:
    if workspace_path is None:
        codex_root = Path(tempfile.mkdtemp(prefix="ralph-codex-home-"))
    else:
        tmp_root = workspace_path / ".agent" / "tmp"
        tmp_root.mkdir(parents=True, exist_ok=True)
        codex_root = Path(tempfile.mkdtemp(prefix="codex-home-", dir=str(tmp_root)))
    maxlen = _allocated_codex_homes.maxlen
    if maxlen is not None and len(_allocated_codex_homes) >= maxlen:
        _allocated_codex_homes.popleft()
    home_str = str(codex_root)
    _allocated_codex_homes.append(home_str)
    _all_allocated_codex_homes.add(home_str)
    if len(_all_allocated_codex_homes) > _ALL_CODEX_HOMES_CAP:
        evicted = next(iter(_all_allocated_codex_homes - {home_str}))
        _all_allocated_codex_homes.discard(evicted)
        logger.warning(
            "codex home lifetime registry over _ALL_CODEX_HOMES_CAP={}; "
            "dropped atexit registration for {} (directory left in place)",
            _ALL_CODEX_HOMES_CAP,
            evicted,
        )
    return codex_root


def _extract_codex_upstream_servers(base: dict[str, object]) -> tuple[UpstreamMcpServer, ...]:
    mcp_servers = {
        name: value
        for name, value in _mapping_at(base, "mcp_servers").items()
        if name != RALPH_MCP_SERVER_NAME
    }
    if not mcp_servers:
        return ()
    return normalize_upstream_mcp_servers(mcp_servers)


__all__ = [
    "CodexConfigError",
    "cleanup_codex_homes",
    "prepare_codex_home",
    "prepare_codex_home_with_upstreams",
    "release_codex_home",
    "synthesize_codex_config",
]

synthesize_codex_config = _synthesize_codex_config
