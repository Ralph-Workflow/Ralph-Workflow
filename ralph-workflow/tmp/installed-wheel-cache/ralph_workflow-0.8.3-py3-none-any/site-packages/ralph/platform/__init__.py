"""Platform detection and OS/architecture identification helpers.

This package detects the host operating system, CPU architecture, Python environment
type, and available package manager. Detection results are used by installers, runtime
configuration, and diagnostic output.

Main entry points:

- ``detect_platform()`` — full platform detection; returns a ``PlatformInfo``.
- ``current_platform()`` — cached singleton ``PlatformInfo`` for the current host.
- ``PlatformInfo`` — composite result (``OperatingSystem``, ``Architecture``,
  ``EnvironmentInfo``, package manager string).
- ``detect_environment()`` / ``EnvironmentInfo`` — virtualenv/conda/pyenv detection.
- ``detect_operating_system()`` / ``OperatingSystem`` — Linux, macOS, or Windows.
- ``detect_architecture()`` / ``Architecture`` — x86_64, arm64, etc.
- ``detect_package_manager()`` — identifies the primary package manager (apt, brew, …).
"""

from .detection import (
    current_platform,
    detect_architecture,
    detect_environment,
    detect_operating_system,
    detect_package_manager,
    detect_platform,
)
from .models import (
    Architecture,
    EnvironmentInfo,
    OperatingSystem,
    PlatformInfo,
)

__all__ = [
    "Architecture",
    "EnvironmentInfo",
    "OperatingSystem",
    "PlatformInfo",
    "current_platform",
    "detect_architecture",
    "detect_environment",
    "detect_operating_system",
    "detect_package_manager",
    "detect_platform",
]
