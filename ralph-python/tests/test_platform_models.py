"""Unit tests for platform data models."""

from __future__ import annotations

from importlib import import_module

platform_module = import_module("ralph.platform")
Architecture = platform_module.Architecture
EnvironmentInfo = platform_module.EnvironmentInfo
OperatingSystem = platform_module.OperatingSystem
PlatformInfo = platform_module.PlatformInfo


def test_platform_info_reports_posix_and_windows_behavior() -> None:
    """PlatformInfo should expose portable behavior helpers."""
    windows_platform = PlatformInfo(
        os=OperatingSystem.WINDOWS,
        architecture=Architecture.X86_64,
    )
    unix_platform = PlatformInfo(
        os=OperatingSystem.LINUX,
        architecture=Architecture.ARM64,
    )

    assert windows_platform.is_posix is False
    assert windows_platform.executable_name("ralph") == "ralph.exe"
    assert unix_platform.is_posix is True
    assert unix_platform.executable_name("ralph") == "ralph"


def test_platform_info_builds_install_command_from_package_manager() -> None:
    """Install command should reflect the detected package manager."""
    profile = PlatformInfo(
        os=OperatingSystem.MACOS,
        architecture=Architecture.ARM64,
        package_manager="brew",
    )

    assert profile.install_command("fd") == ["brew", "install", "fd"]


def test_platform_info_summary_mentions_environment_markers() -> None:
    """Summary should include notable environment details."""
    profile = PlatformInfo(
        os=OperatingSystem.LINUX,
        architecture=Architecture.X86_64,
        environment=EnvironmentInfo(ci=True, container=True, wsl=True),
        package_manager="apt",
    )

    assert profile.summary() == "linux/x86_64 [ci, container, wsl] via apt"
