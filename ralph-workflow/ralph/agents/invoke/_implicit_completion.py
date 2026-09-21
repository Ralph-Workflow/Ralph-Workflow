"""Scoped implicit-completion policy for transports that cannot resume."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.phases.required_artifacts import RequiredArtifact


def is_implicit_development_exit(required_artifact: RequiredArtifact | None) -> bool:
    return (
        required_artifact is not None
        and required_artifact.phase == "development"
        and required_artifact.artifact_type == "development_result"
    )


__all__ = ["is_implicit_development_exit"]
