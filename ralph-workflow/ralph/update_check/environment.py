"""Detect how Ralph Workflow was installed and how to upgrade it.

All inputs are injected (the package file location, the environment mapping, a
``path_exists`` probe, and the frozen flag) so detection is a pure function that
each install branch can be unit-tested without touching the real filesystem.
"""

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
    """Detected install method and the command to upgrade it."""

    kind: InstallKind
    upgrade_command: str


def _find_source_repo_root(package_file: Path, path_exists: Callable[[Path], bool]) -> Path | None:
    """Walk upward from ``package_file`` looking for a ``.git`` work tree root."""
    for directory in package_file.resolve().parents:
        if path_exists(directory / ".git"):
            return directory
    return None


def _looks_like_pipx(parts: tuple[str, ...], environ: Mapping[str, str], raw: str) -> bool:
    pipx_home = environ.get("PIPX_HOME")
    if pipx_home and raw.startswith(str(Path(pipx_home))):
        return True
    return "pipx" in parts and "venvs" in parts


def _looks_like_uv_tool(parts: tuple[str, ...]) -> bool:
    # uv installs tools under ``.../uv/tools/<name>/...``.
    for index in range(len(parts) - 1):
        if parts[index] == "uv" and parts[index + 1] == "tools":
            return True
    return False


def _classify_located(
    file_path: Path,
    environ: Mapping[str, str],
    path_exists: Callable[[Path], bool],
) -> InstallInfo:
    """Classify an install whose package file location is known.

    Order is deliberate: an explicit packaging tool (source checkout, pipx, uv)
    wins over Docker, which wins over a plain pip install.
    """
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
    """Classify the install and return the matching upgrade command.

    A frozen bundle and an undeterminable location are handled here; every other
    case is delegated to :func:`_classify_located`.
    """
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
