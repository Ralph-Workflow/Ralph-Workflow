"""The install-method enum for the update nagger.

Split out of :mod:`ralph.update_check.environment` so each module owns a single
public class (repo structure policy); ``environment`` re-exports it.
"""

from __future__ import annotations

from enum import StrEnum


class InstallKind(StrEnum):
    """How the running ``ralph`` was installed."""

    SOURCE = "source"
    PIPX = "pipx"
    UV_TOOL = "uv-tool"
    DOCKER = "docker"
    FROZEN = "frozen"
    PIP = "pip"
    UNKNOWN = "unknown"
