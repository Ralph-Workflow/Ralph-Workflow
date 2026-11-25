from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Final

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import atomic_write_bytes_if_changed
from ralph.mcp.transport.private_config_root import prepare_private_config_root

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

AGY_OAUTH_FILE: Final[str] = "antigravity-oauth-token"

AGY_AUTH_ARTIFACTS: Final[tuple[str, ...]] = (AGY_OAUTH_FILE, "settings.json", "installation_id")

AGY_CONFIG_RELATIVE_DIR: Final[Path] = Path(".gemini") / "antigravity-cli"

_PRIVATE_MODE: Final[int] = 0o600


def agy_auth_vault_dir(env: Mapping[str, str]) -> Path:
    data_home = env.get("XDG_DATA_HOME")
    base = (
        Path(data_home).expanduser()
        if data_home
        else Path(env.get("HOME", str(Path.home()))).expanduser() / ".local" / "share"
    )
    return base / "ralph-workflow" / "agy-auth"


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _copy_preserving_mtime(source: Path, destination: Path) -> None:
    stat = source.stat()
    payload = DEFAULT_FILE_BACKEND.read_bytes(source)
    DEFAULT_FILE_BACKEND.mkdir(destination.parent, parents=True, exist_ok=True)
    atomic_write_bytes_if_changed(
        DEFAULT_FILE_BACKEND,
        destination,
        payload,
        tmp_path=destination.with_name(f".{destination.name}.tmp"),
    )
    destination.chmod(_PRIVATE_MODE)
    os.utime(destination, (stat.st_atime, stat.st_mtime))


def _copy_if_newer(source: Path, destination: Path) -> None:
    source_mtime = _mtime(source)
    if source_mtime is None:
        return
    destination_mtime = _mtime(destination)
    if destination_mtime is not None and destination_mtime >= source_mtime:
        return
    try:
        _copy_preserving_mtime(source, destination)
    except OSError:
        return


def project_agy_auth(source_root: Path, destination_root: Path, vault_root: Path) -> None:
    for artifact in AGY_AUTH_ARTIFACTS:
        _copy_if_newer(source_root / artifact, vault_root / artifact)
        _copy_if_newer(vault_root / artifact, destination_root / artifact)


def write_back_agy_auth(private_root: Path, vault_root: Path) -> None:
    _copy_if_newer(private_root / AGY_OAUTH_FILE, vault_root / AGY_OAUTH_FILE)


def prepare_agy_auth_home(env: Mapping[str, str]) -> tuple[Path, Callable[[], None]]:
    source_home = Path(env.get("HOME", str(Path.home()))).expanduser()
    vault = agy_auth_vault_dir(env)
    private_home, release_private_home = prepare_private_config_root((), prefix="ralph-agy-auth-")
    private_agy = private_home / AGY_CONFIG_RELATIVE_DIR
    project_agy_auth(source_home / AGY_CONFIG_RELATIVE_DIR, private_agy, vault)

    def cleanup() -> None:
        write_back_agy_auth(private_agy, vault)
        release_private_home()

    return private_home, cleanup


__all__ = [
    "AGY_AUTH_ARTIFACTS",
    "AGY_CONFIG_RELATIVE_DIR",
    "AGY_OAUTH_FILE",
    "agy_auth_vault_dir",
    "prepare_agy_auth_home",
    "project_agy_auth",
    "write_back_agy_auth",
]
