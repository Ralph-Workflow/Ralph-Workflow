"""Unit tests for platform detection helpers."""

from __future__ import annotations

from importlib import import_module

platform_module = import_module("ralph.platform")
Architecture = platform_module.Architecture
OperatingSystem = platform_module.OperatingSystem
detect_architecture = platform_module.detect_architecture
detect_environment = platform_module.detect_environment
detect_operating_system = platform_module.detect_operating_system
detect_package_manager = platform_module.detect_package_manager
detect_platform = platform_module.detect_platform


def test_detect_operating_system_normalizes_common_system_names() -> None:
    """OS detection should normalize platform.system() values."""
    assert detect_operating_system("Darwin") is OperatingSystem.MACOS
    assert detect_operating_system("Linux") is OperatingSystem.LINUX
    assert detect_operating_system("Windows") is OperatingSystem.WINDOWS
    assert detect_operating_system("Haiku") is OperatingSystem.UNKNOWN


def test_detect_architecture_normalizes_machine_names() -> None:
    """Architecture detection should map common aliases to stable values."""
    assert detect_architecture("x86_64") is Architecture.X86_64
    assert detect_architecture("AMD64") is Architecture.X86_64
    assert detect_architecture("aarch64") is Architecture.ARM64
    assert detect_architecture("mips") is Architecture.UNKNOWN


def test_detect_environment_tracks_ci_container_wsl_and_ssh() -> None:
    """Environment detection should surface notable runtime markers."""
    environment = detect_environment(
        env={"CI": "true", "SSH_CONNECTION": "1 2 3 4", "CODESPACES": "true"},
        os_name=OperatingSystem.LINUX,
        release="5.15.0-microsoft-standard-WSL2",
        cgroup_text="12:memory:/docker/abc123",
    )

    assert environment.ci is True
    assert environment.container is True
    assert environment.wsl is True
    assert environment.codespaces is True
    assert environment.ssh is True


def test_detect_package_manager_uses_path_search_order() -> None:
    """Package manager detection should respect OS-specific preferences."""
    assert detect_package_manager(OperatingSystem.MACOS, search_path="/bin") is None
    assert (
        detect_package_manager(
            OperatingSystem.LINUX,
            search_path="/usr/bin",
            command_lookup=lambda command, path: command in {"apt-get", "yum"},
        )
        == "apt-get"
    )
    assert (
        detect_package_manager(
            OperatingSystem.WINDOWS,
            search_path="C:/bin",
            command_lookup=lambda command, path: command == "winget",
        )
        == "winget"
    )


def test_detect_platform_builds_complete_platform_profile() -> None:
    """Top-level detection should combine OS, architecture, environment, and tools."""
    profile = detect_platform(
        system_name="Darwin",
        machine_name="arm64",
        env={"GITHUB_ACTIONS": "true"},
        search_path="/opt/homebrew/bin",
        command_lookup=lambda command, path: command == "brew",
    )

    assert profile.os is OperatingSystem.MACOS
    assert profile.architecture is Architecture.ARM64
    assert profile.environment.ci is True
    assert profile.package_manager == "brew"
    assert profile.install_command("ripgrep") == ["brew", "install", "ripgrep"]
