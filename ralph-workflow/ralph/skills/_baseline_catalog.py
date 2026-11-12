from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BaselineCapability:
    name: str
    description: str
    tier: str


STATIC_BUILTIN_CAPABILITIES: tuple[BaselineCapability, ...] = (
    BaselineCapability(
        "workspace_ops",
        "Workspace and local file operations",
        "static_builtin",
    ),
    BaselineCapability(
        "git_read_ops",
        "Git read, status, diff, and log operations",
        "static_builtin",
    ),
    BaselineCapability(
        "artifact_ops",
        "Artifact submission support",
        "static_builtin",
    ),
    BaselineCapability(
        "plan_read",
        "Plan-reading support",
        "static_builtin",
    ),
    BaselineCapability(
        "media_read",
        "Image and media file read support",
        "static_builtin",
    ),
)

__all__ = [
    "STATIC_BUILTIN_CAPABILITIES",
    "BaselineCapability",
]
