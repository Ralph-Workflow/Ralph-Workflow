from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from ralph.git.subprocess_runner import run_git

GUARDED_SHARED_CONFIG_KEYS: tuple[str, ...] = ("core.bare", "core.worktree")
_GITDIR_PREFIX = "gitdir:"
_GUARD_ERRORS = (OSError, RuntimeError, ValueError, UnicodeDecodeError)


@dataclass(frozen=True)
class SharedConfigSnapshot:
    config_path: Path
    content: bytes


def _dot_git(root: Path) -> Path | None:
    for candidate in (root, *root.parents):
        dot_git = candidate / ".git"
        if dot_git.exists():
            return dot_git
    return None


def shared_config_path(root: Path) -> Path | None:
    dot_git = _dot_git(root.resolve())
    if dot_git is None:
        return None
    if dot_git.is_dir():
        return dot_git / "config"
    pointer = dot_git.read_text(encoding="utf-8").strip()
    if not pointer.startswith(_GITDIR_PREFIX):
        return None
    git_dir = Path(pointer.removeprefix(_GITDIR_PREFIX).strip())
    if not git_dir.is_absolute():
        git_dir = dot_git.parent / git_dir
    commondir = git_dir / "commondir"
    if not commondir.is_file():
        return git_dir / "config"
    common = Path(commondir.read_text(encoding="utf-8").strip())
    if not common.is_absolute():
        common = git_dir / common
    return common.resolve() / "config"


def snapshot_shared_config(root: Path) -> SharedConfigSnapshot | None:
    try:
        config_path = shared_config_path(root)
        if config_path is None or not config_path.is_file():
            return None
        return SharedConfigSnapshot(config_path, config_path.read_bytes())
    except _GUARD_ERRORS:
        return None


def guarded_core_values(content: bytes) -> dict[str, str | None]:
    values: dict[str, str | None] = dict.fromkeys(GUARDED_SHARED_CONFIG_KEYS)
    section = ""
    for raw_line in content.decode("utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            section = line.strip("[]").split()[0].lower() if line.strip("[]") else ""
            continue
        name, _sep, value = line.partition("=")
        key = f"{section}.{name.strip().lower()}"
        if key in values:
            values[key] = value.strip().strip('"') if _sep else "true"
    return values


def _is_breaking(key: str, before: str | None, after: str | None) -> bool:
    if before == after:
        return False
    if key == "core.bare":
        return (after or "").lower() == "true"
    return after is not None


def _restore(config_path: Path, key: str, before: str | None) -> None:
    args = (
        ("config", "--file", str(config_path), "--unset-all", key)
        if before is None
        else ("config", "--file", str(config_path), key, before)
    )
    run_git(args, cwd=config_path.parent, label="shared-config-guard:restore")


def repair_shared_config(snapshot: SharedConfigSnapshot | None, *, attempt: str) -> tuple[str, ...]:
    if snapshot is None:
        return ()
    try:
        current = snapshot.config_path.read_bytes()
        if current == snapshot.content:
            return ()
        before = guarded_core_values(snapshot.content)
        after = guarded_core_values(current)
        repaired = tuple(
            key for key in GUARDED_SHARED_CONFIG_KEYS if _is_breaking(key, before[key], after[key])
        )
        for key in repaired:
            _restore(snapshot.config_path, key, before[key])
    except _GUARD_ERRORS as exc:
        logger.warning("could not check the shared git config after {}: {}", attempt, exc)
        return ()
    if repaired:
        logger.warning(
            "agent attempt {} changed the shared git config {} ({}); restored the values from "
            "before the attempt",
            attempt,
            snapshot.config_path,
            ", ".join(f"{key}: {after[key]!r} -> {before[key]!r}" for key in repaired),
        )
    return repaired


__all__ = [
    "GUARDED_SHARED_CONFIG_KEYS",
    "SharedConfigSnapshot",
    "guarded_core_values",
    "repair_shared_config",
    "shared_config_path",
    "snapshot_shared_config",
]
