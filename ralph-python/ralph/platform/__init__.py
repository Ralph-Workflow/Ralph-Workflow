"""Platform detection and platform-specific behavior helpers."""

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
