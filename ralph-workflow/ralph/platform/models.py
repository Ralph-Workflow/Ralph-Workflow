"""Data models for platform detection and platform-specific behavior."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class OperatingSystem(StrEnum):
    """Normalized operating system names."""

    MACOS = "macos"
    LINUX = "linux"
    WINDOWS = "windows"
    UNKNOWN = "unknown"


class Architecture(StrEnum):
    """Normalized CPU architecture names."""

    X86_64 = "x86_64"
    ARM64 = "arm64"
    X86 = "x86"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EnvironmentInfo:
    """Detected runtime environment traits."""

    ci: bool = False
    container: bool = False
    wsl: bool = False
    codespaces: bool = False
    ssh: bool = False

    def markers(self) -> list[str]:
        """Return enabled environment markers in display order."""
        markers: list[str] = []
        if self.ci:
            markers.append("ci")
        if self.container:
            markers.append("container")
        if self.wsl:
            markers.append("wsl")
        if self.codespaces:
            markers.append("codespaces")
        if self.ssh:
            markers.append("ssh")
        return markers


@dataclass(frozen=True)
class PlatformInfo:
    """Complete platform profile used by Ralph's Python implementation."""

    os: OperatingSystem = OperatingSystem.UNKNOWN
    architecture: Architecture = Architecture.UNKNOWN
    environment: EnvironmentInfo = field(default_factory=EnvironmentInfo)
    package_manager: str | None = None

    @property
    def is_posix(self) -> bool:
        """Return True for POSIX-like platforms."""
        return self.os in {OperatingSystem.MACOS, OperatingSystem.LINUX}

    def executable_name(self, command: str) -> str:
        """Return the executable name for the current platform."""
        if self.os is OperatingSystem.WINDOWS and not command.endswith(".exe"):
            return f"{command}.exe"
        return command

    def install_command(self, package: str) -> list[str] | None:
        """Return the package installation command for the detected package manager."""
        if self.package_manager is None:
            return None

        installers: dict[str, list[str]] = {
            "brew": ["brew", "install"],
            "apt": ["apt", "install", "-y"],
            "apt-get": ["apt-get", "install", "-y"],
            "dnf": ["dnf", "install", "-y"],
            "yum": ["yum", "install", "-y"],
            "pacman": ["pacman", "-S", "--noconfirm"],
            "winget": ["winget", "install", "--exact"],
            "choco": ["choco", "install", "-y"],
            "scoop": ["scoop", "install"],
        }
        prefix = installers.get(self.package_manager)
        if prefix is None:
            return None
        return [*prefix, package]

    def summary(self) -> str:
        """Return a concise human-readable platform summary."""
        summary = f"{self.os.value}/{self.architecture.value}"
        markers = self.environment.markers()
        if markers:
            summary = f"{summary} [{', '.join(markers)}]"
        if self.package_manager is not None:
            summary = f"{summary} via {self.package_manager}"
        return summary
