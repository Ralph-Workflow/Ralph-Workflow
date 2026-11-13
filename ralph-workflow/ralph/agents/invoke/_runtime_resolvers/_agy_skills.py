from __future__ import annotations

from pathlib import Path

from loguru import logger

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND

_AGY_SKILL_ROOT_PATHS: tuple[Path, ...] = (
    Path(".gemini/antigravity-cli/skills"),
    Path(".gemini/config/skills"),
)


def project_agy_skill_roots(source_home: Path, private_home: Path) -> None:
    for root_path in _AGY_SKILL_ROOT_PATHS:
        _project_skill_root(source_home / root_path, private_home / root_path)


def _project_skill_root(source_root: Path, private_root: Path) -> None:
    if not source_root.is_dir():
        return
    DEFAULT_FILE_BACKEND.mkdir(private_root.parent, parents=True, exist_ok=True)
    try:
        private_root.symlink_to(source_root, target_is_directory=True)
    except OSError as exc:
        logger.warning(
            "The Antigravity skill root {} was not projected into the private home: {}",
            source_root,
            type(exc).__name__,
        )


__all__ = ["project_agy_skill_roots"]
