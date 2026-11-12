from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.update_check._install_kind import InstallKind

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

PACKAGE_NAME = "ralph-workflow"
PYPI_PROJECT_URL = "https://pypi.org/project/ralph-workflow/"


@dataclass(frozen=True)
class InstallInfo:
    kind: InstallKind
    upgrade_command: str


def _find_source_repo_root(package_file: Path, path_exists: Callable[[Path], bool]) -> Path | None:
    for directory in package_file.parents:
        if path_exists(directory / ".git"):
            return directory
    return None


def _looks_like_pipx(parts: tuple[str, ...], environ: Mapping[str, str], raw: str) -> bool:
    pipx_home = environ.get("PIPX_HOME")
    if pipx_home and raw.startswith(str(Path(pipx_home))):
        return True
    return "pipx" in parts and "venvs" in parts


def _looks_like_uv_tool(parts: tuple[str, ...]) -> bool:
    for index in range(len(parts) - 1):
        if parts[index] == "uv" and parts[index + 1] == "tools":
            return True
    return False


def _classify_located(
    file_path: Path,
    environ: Mapping[str, str],
    path_exists: Callable[[Path], bool],
) -> InstallInfo:
    repo_root = _find_source_repo_root(file_path, path_exists)
    if repo_root is not None:
        return InstallInfo(InstallKind.SOURCE, f'cd "{repo_root}" && git pull origin main')

    parts = file_path.parts
    if _looks_like_pipx(parts, environ, str(file_path)):
        return InstallInfo(InstallKind.PIPX, f"pipx upgrade {PACKAGE_NAME}")

    if _looks_like_uv_tool(parts):
        return InstallInfo(InstallKind.UV_TOOL, f"uv tool upgrade {PACKAGE_NAME}")

    if path_exists(Path("/.dockerenv")):
        return InstallInfo(
            InstallKind.DOCKER,
            "Re-pull or rebuild your Ralph Workflow container image",
        )

    return InstallInfo(InstallKind.PIP, f"pip install --upgrade {PACKAGE_NAME}")


def detect_install(
    *,
    package_file: str | None,
    environ: Mapping[str, str],
    is_frozen: bool,
    path_exists: Callable[[Path], bool],
) -> InstallInfo:
    if is_frozen:
        return InstallInfo(
            InstallKind.FROZEN,
            f"Download the latest release: {PYPI_PROJECT_URL}",
        )
    if package_file is None:
        return InstallInfo(
            InstallKind.UNKNOWN,
            f"A newer version is available - see {PYPI_PROJECT_URL}",
        )
    return _classify_located(Path(package_file), environ, path_exists)
