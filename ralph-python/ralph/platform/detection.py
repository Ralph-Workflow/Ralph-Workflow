"""Helpers for platform and environment detection."""

from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, Unpack

from .models import (
    Architecture,
    EnvironmentInfo,
    OperatingSystem,
    PlatformInfo,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


class DetectPlatformKwargs(TypedDict, total=False):
    system_name: str | None
    machine_name: str | None
    env: "Mapping[str, str] | None"
    release: str | None
    cgroup_text: str | None
    search_path: str | None
    command_lookup: "Callable[[str, str | None], bool] | None"

PACKAGE_MANAGER_CANDIDATES: dict[OperatingSystem, tuple[str, ...]] = {
    OperatingSystem.MACOS: ("brew",),
    OperatingSystem.LINUX: ("apt", "apt-get", "dnf", "yum", "pacman"),
    OperatingSystem.WINDOWS: ("winget", "choco", "scoop"),
    OperatingSystem.UNKNOWN: (),
}


def _default_command_lookup(command: str, search_path: str | None) -> bool:
    return shutil.which(command, path=search_path) is not None


def _normalize(value: str | None) -> str:
    return (value or "").strip().lower()


def detect_operating_system(system_name: str | None = None) -> OperatingSystem:
    """Normalize ``platform.system()`` into a stable OS enum."""
    normalized = _normalize(system_name or platform.system())
    if normalized == "darwin":
        return OperatingSystem.MACOS
    if normalized == "linux":
        return OperatingSystem.LINUX
    if normalized == "windows":
        return OperatingSystem.WINDOWS
    return OperatingSystem.UNKNOWN


def detect_architecture(machine_name: str | None = None) -> Architecture:
    """Normalize ``platform.machine()`` into a stable architecture enum."""
    normalized = _normalize(machine_name or platform.machine())
    if normalized in {"x86_64", "amd64"}:
        return Architecture.X86_64
    if normalized in {"arm64", "aarch64"}:
        return Architecture.ARM64
    if normalized in {"x86", "i386", "i686"}:
        return Architecture.X86
    return Architecture.UNKNOWN


def _read_cgroup_text(proc_root: Path = Path("/proc")) -> str:
    cgroup_path = proc_root / "1" / "cgroup"
    try:
        return cgroup_path.read_text(encoding="utf-8")
    except OSError:
        return ""


def detect_environment(
    env: Mapping[str, str] | None = None,
    *,
    os_name: OperatingSystem | None = None,
    release: str | None = None,
    cgroup_text: str | None = None,
    proc_root: Path = Path("/proc"),
) -> EnvironmentInfo:
    """Detect runtime environment markers such as CI, containers, and WSL."""
    env_map = env or os.environ
    detected_os = os_name or detect_operating_system()
    normalized_release = _normalize(release or platform.release())
    cgroup = (
        _normalize(cgroup_text)
        if cgroup_text is not None
        else _normalize(_read_cgroup_text(proc_root))
    )

    ci = any(
        _normalize(env_map.get(name)) in {"1", "true", "yes"}
        for name in ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE")
    )
    codespaces = _normalize(env_map.get("CODESPACES")) in {"1", "true", "yes"}
    ssh = any(name in env_map for name in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"))
    container = any(
        _normalize(env_map.get(name)) in {"1", "true", "yes", "docker", "podman"}
        for name in ("container", "DOCKER_CONTAINER", "KUBERNETES_SERVICE_HOST")
    ) or any(marker in cgroup for marker in ("docker", "containerd", "kubepods", "podman"))
    wsl = detected_os is OperatingSystem.LINUX and "microsoft" in normalized_release

    return EnvironmentInfo(
        ci=ci,
        container=container,
        wsl=wsl,
        codespaces=codespaces,
        ssh=ssh,
    )


def detect_package_manager(
    os_name: OperatingSystem | None = None,
    *,
    search_path: str | None = None,
    command_lookup: Callable[[str, str | None], bool] | None = None,
) -> str | None:
    """Detect the first supported package manager available on the current OS."""
    detected_os = os_name or detect_operating_system()
    lookup = command_lookup or _default_command_lookup

    for candidate in PACKAGE_MANAGER_CANDIDATES[detected_os]:
        if lookup(candidate, search_path):
            return candidate
    return None


def detect_platform(**kwargs: Unpack[DetectPlatformKwargs]) -> PlatformInfo:
    """Build a complete platform profile for the current runtime."""
    system_name = kwargs.get("system_name")
    machine_name = kwargs.get("machine_name")
    env = kwargs.get("env")
    release = kwargs.get("release")
    cgroup_text = kwargs.get("cgroup_text")
    search_path = kwargs.get("search_path")
    command_lookup = kwargs.get("command_lookup")
    os_name = detect_operating_system(system_name)
    return PlatformInfo(
        os=os_name,
        architecture=detect_architecture(machine_name),
        environment=detect_environment(
            env,
            os_name=os_name,
            release=release,
            cgroup_text=cgroup_text,
        ),
        package_manager=detect_package_manager(
            os_name,
            search_path=search_path,
            command_lookup=command_lookup,
        ),
    )


def current_platform() -> PlatformInfo:
    """Return the detected platform for the current runtime."""
    return detect_platform()
