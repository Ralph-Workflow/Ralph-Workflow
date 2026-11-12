from __future__ import annotations

from enum import StrEnum


class InstallKind(StrEnum):
    SOURCE = "source"
    PIPX = "pipx"
    UV_TOOL = "uv-tool"
    DOCKER = "docker"
    FROZEN = "frozen"
    PIP = "pip"
    UNKNOWN = "unknown"
