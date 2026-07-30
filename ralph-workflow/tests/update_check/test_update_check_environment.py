"""Install-environment detection and upgrade-command mapping."""

from __future__ import annotations

from pathlib import Path

from ralph.update_check.environment import (
    InstallKind,
    detect_install,
)


def _exists_none(_path: Path) -> bool:
    return False


def _exists_set(paths: set[str]):
    def _exists(path: Path) -> bool:
        return str(path) in paths

    return _exists


def test_frozen_bundle_points_to_release_download() -> None:
    info = detect_install(
        package_file="/opt/ralph/ralph/__init__.py",
        environ={},
        is_frozen=True,
        path_exists=_exists_none,
    )
    assert info.kind is InstallKind.FROZEN
    assert "pypi.org/project/ralph-workflow" in info.upgrade_command


def test_unknown_when_package_file_missing() -> None:
    info = detect_install(
        package_file=None,
        environ={},
        is_frozen=False,
        path_exists=_exists_none,
    )
    assert info.kind is InstallKind.UNKNOWN
    assert "newer version" in info.upgrade_command


def test_source_checkout_suggests_git_pull_with_repo_path() -> None:
    pkg = "/Users/dev/Ralph-Workflow/ralph-workflow/ralph/__init__.py"
    path_exists = _exists_set({"/Users/dev/Ralph-Workflow/.git"})
    info = detect_install(
        package_file=pkg,
        environ={},
        is_frozen=False,
        path_exists=path_exists,
    )
    assert info.kind is InstallKind.SOURCE
    assert info.upgrade_command == 'cd "/Users/dev/Ralph-Workflow" && git pull origin main'


def test_pipx_install_detected_by_path() -> None:
    info = detect_install(
        package_file="/home/u/.local/pipx/venvs/ralph-workflow/lib/python3.12/site-packages/ralph/__init__.py",
        environ={},
        is_frozen=False,
        path_exists=_exists_none,
    )
    assert info.kind is InstallKind.PIPX
    assert info.upgrade_command == "pipx upgrade ralph-workflow"


def test_pipx_install_detected_by_pipx_home_env() -> None:
    info = detect_install(
        package_file="/opt/px/ralph-workflow/lib/site-packages/ralph/__init__.py",
        environ={"PIPX_HOME": "/opt/px"},
        is_frozen=False,
        path_exists=_exists_none,
    )
    assert info.kind is InstallKind.PIPX


def test_uv_tool_install_detected_by_path() -> None:
    info = detect_install(
        package_file="/home/u/.local/share/uv/tools/ralph-workflow/lib/python3.12/site-packages/ralph/__init__.py",
        environ={},
        is_frozen=False,
        path_exists=_exists_none,
    )
    assert info.kind is InstallKind.UV_TOOL
    assert info.upgrade_command == "uv tool upgrade ralph-workflow"


def test_docker_detected_when_dockerenv_present() -> None:
    info = detect_install(
        package_file="/usr/lib/python3.12/site-packages/ralph/__init__.py",
        environ={},
        is_frozen=False,
        path_exists=_exists_set({"/.dockerenv"}),
    )
    assert info.kind is InstallKind.DOCKER
    assert "container image" in info.upgrade_command


def test_plain_pip_is_the_fallback() -> None:
    info = detect_install(
        package_file="/usr/lib/python3.12/site-packages/ralph/__init__.py",
        environ={},
        is_frozen=False,
        path_exists=_exists_none,
    )
    assert info.kind is InstallKind.PIP
    assert info.upgrade_command == "pip install --upgrade ralph-workflow"


def test_source_checkout_wins_over_docker() -> None:
    pkg = "/src/Ralph-Workflow/ralph-workflow/ralph/__init__.py"
    path_exists = _exists_set({"/src/Ralph-Workflow/.git", "/.dockerenv"})
    info = detect_install(
        package_file=pkg,
        environ={},
        is_frozen=False,
        path_exists=path_exists,
    )
    assert info.kind is InstallKind.SOURCE
