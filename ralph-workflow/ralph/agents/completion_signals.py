"""Completion signal evaluation for OpenCode agent exits.

evaluate_completion() inspects the workspace artifacts directory and the raw
NDJSON output to determine whether an OpenCode agent run produced the required
phase artifact or explicitly declared completion via the declare_complete MCP
tool. Explicit completion and artifact presence are separate signals; the
explicit-complete flag is never auto-set just because a phase has no required
artifact entry.

Phases without a required artifact entry in REQUIRED_ARTIFACTS return
required_artifact_present=False. OpenCode agents running such phases must still
call declare_complete explicitly rather than relying on implicit success.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_EXPLICIT_COMPLETION_MARKER = "Task declared complete:"


@dataclass(frozen=True)
class CompletionSignals:
    """Signals that indicate whether an agent run actually completed its work.

    Attributes:
        explicit_complete: True when the agent called the declare_complete MCP
            tool successfully (independent of artifact presence).
        required_artifact_present: True when the required phase artifact exists
            on disk. False when the phase has no registered required artifact or
            the artifact file does not yet exist.
        artifact_types: Tuple of artifact type names found.
    """

    explicit_complete: bool
    required_artifact_present: bool
    artifact_types: tuple[str, ...]


def extract_explicit_completion(raw_output: list[str]) -> bool:
    """Return True if raw NDJSON output contains a successful declare_complete call.

    Detects the unique marker produced by handle_declare_complete() in
    ralph/mcp/tools/coordination.py. The marker string only appears in the
    output when the agent successfully calls the declare_complete MCP tool.

    Args:
        raw_output: Raw NDJSON lines from the agent subprocess stdout.

    Returns:
        True if the declare_complete marker is found in any output line.
    """
    return any(_EXPLICIT_COMPLETION_MARKER in line for line in raw_output)


def evaluate_completion(
    workspace: Path,
    phase: str,
    raw_output: list[str] | None = None,
) -> CompletionSignals:
    """Check whether the agent run produced a required artifact or explicit completion.

    explicit_complete is set from scanning raw_output for the declare_complete
    MCP tool marker, independently of artifact presence. required_artifact_present
    is True only when the artifact file exists on disk for phases that have a
    registered required artifact. Phases without a registered required artifact
    always return required_artifact_present=False so OpenCode agents cannot
    implicitly succeed — they must call declare_complete explicitly.

    Args:
        workspace: Workspace root path.
        phase: Pipeline phase name to look up.
        raw_output: Raw NDJSON lines from agent stdout for explicit-completion detection.

    Returns:
        CompletionSignals reflecting current artifact state and explicit completion.
    """
    from ralph.phases.required_artifacts import REQUIRED_ARTIFACTS  # noqa: PLC0415

    explicit = extract_explicit_completion(raw_output or [])
    ra = REQUIRED_ARTIFACTS.get(phase)
    if ra is None:
        return CompletionSignals(
            explicit_complete=explicit,
            required_artifact_present=False,
            artifact_types=(),
        )
    artifact_path = workspace / ra.json_path
    present = artifact_path.exists()
    return CompletionSignals(
        explicit_complete=explicit,
        required_artifact_present=present,
        artifact_types=(ra.artifact_type,) if present else (),
    )


__all__ = [
    "CompletionSignals",
    "evaluate_completion",
    "extract_explicit_completion",
]
