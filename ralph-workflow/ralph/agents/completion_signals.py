"""Completion signal evaluation for OpenCode agent exits.

evaluate_completion() inspects the workspace artifacts directory to determine
whether an OpenCode agent run produced the required phase artifact, making
artifact submission the primary success criterion rather than subprocess exit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class CompletionSignals:
    """Signals that indicate whether an agent run actually completed its work.

    Attributes:
        explicit_complete: True when the run explicitly signalled completion.
        required_artifact_present: True when the required phase artifact exists.
        artifact_types: Tuple of artifact type names found.
    """

    explicit_complete: bool
    required_artifact_present: bool
    artifact_types: tuple[str, ...]


def evaluate_completion(workspace: Path, phase: str) -> CompletionSignals:
    """Check whether the required artifact for phase exists in workspace.

    Args:
        workspace: Workspace root path.
        phase: Pipeline phase name to look up.

    Returns:
        CompletionSignals reflecting current artifact state.
    """
    from ralph.phases.required_artifacts import REQUIRED_ARTIFACTS  # noqa: PLC0415

    ra = REQUIRED_ARTIFACTS.get(phase)
    if ra is None:
        return CompletionSignals(
            explicit_complete=True,
            required_artifact_present=True,
            artifact_types=(),
        )
    artifact_path = workspace / ra.json_path
    present = artifact_path.exists()
    return CompletionSignals(
        explicit_complete=present,
        required_artifact_present=present,
        artifact_types=(ra.artifact_type,) if present else (),
    )


__all__ = [
    "CompletionSignals",
    "evaluate_completion",
]
