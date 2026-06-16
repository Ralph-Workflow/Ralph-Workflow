"""Completion signal evaluation for OpenCode agent exits.

evaluate_completion() inspects the workspace artifacts directory and the raw
NDJSON output to determine whether an OpenCode agent run produced the required
phase artifact or explicitly declared completion via the declare_complete MCP
tool. Explicit completion and artifact presence are separate signals; the
explicit-complete flag is never auto-set just because a phase has no required
artifact entry.

Phases whose pipeline definition marks the output artifact optional
(`artifact_required=False`) are treated as terminal on a clean exit even when no
artifact is produced and no explicit declare_complete call is made. The artifact
provides context only; its absence does not gate phase success. A present optional
artifact is still fully validated.

Phases without any artifact contract return required_artifact_present=False.
OpenCode agents running such phases must still call declare_complete explicitly
rather than relying on implicit success.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.canonical_submit import promote_fallback_artifact
from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.mcp.tools.artifact import ArtifactHandlerDeps
    from ralph.phases.required_artifacts import RequiredArtifact

from ralph.mcp.tools.coordination import COMPLETION_SENTINEL_RELPATHFMT

_EXPLICIT_COMPLETION_MARKER = "Task declared complete:"
_COMPLETION_SENTINEL_RELPATHFMT = COMPLETION_SENTINEL_RELPATHFMT


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
        terminal_ack_seen: True when a child_terminal lifecycle ACK was received
            from the OpenCode transport.
        artifact_optional: True when the phase marks its output artifact optional
            (artifact_required=False). A clean exit is terminal even without the
            artifact or an explicit declare_complete call.
        completion_sentinel_present: True when the run-scoped completion sentinel
            written by handle_declare_complete exists on disk. This is the
            authoritative proof that the agent actually invoked the
            declare_complete MCP tool; the plain-text marker alone can be
            spoofed by agent output.
    """

    explicit_complete: bool
    required_artifact_present: bool
    artifact_types: tuple[str, ...]
    terminal_ack_seen: bool = False
    artifact_optional: bool = False
    completion_sentinel_present: bool = False


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


def _check_completion_sentinel(
    workspace: Path,
    run_id: str | None,
    *,
    _read_fn: Callable[[Path], str] | None = None,
) -> bool:
    """Return True when the run-scoped completion sentinel exists."""
    if run_id is None:
        return False
    sentinel_path = workspace / _COMPLETION_SENTINEL_RELPATHFMT.format(run_id=run_id)
    read_fn = _read_fn or (lambda path: path.read_text(encoding="utf-8"))
    try:
        read_fn(sentinel_path)
    except (FileNotFoundError, OSError):
        return False
    return True


def _artifact_is_schema_valid(artifact_path: Path) -> bool:
    """Return True when the artifact file exists, parses as JSON, and is a non-empty dict."""
    if not artifact_path.exists():
        return False
    try:
        content = artifact_path.read_text(encoding="utf-8")
        parsed = cast("object", json.loads(content))
        return isinstance(parsed, dict) and len(parsed) > 0
    except (OSError, json.JSONDecodeError, ValueError):
        return False


def is_artifact_submitted(
    workspace: Path,
    run_id: str,
    artifact_type: str,
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> bool:
    """Return True when a canonical receipt exists or can be promoted from fallback.

    This is the completion-signal layer's single entry point for artifact
    presence. It first checks for a receipt; if none exists it attempts to
    promote a fallback file written by the agent (``.agent/tmp/<type>.json`` or
    ``.agent/artifacts/<type>.json``) through the canonical submit path so a
    receipt is stamped.
    """
    if artifact_receipt_present(workspace, run_id, artifact_type):
        return True

    result = promote_fallback_artifact(workspace, artifact_type, deps=deps, run_id=run_id)
    return result is not None and result.receipt_path is not None


def evaluate_completion(
    workspace: Path,
    raw_output: list[str] | None = None,
    *,
    required_artifact: RequiredArtifact | None = None,
    run_id: str | None = None,
) -> CompletionSignals:
    """Check whether the agent run produced a required artifact or explicit completion.

    explicit_complete is set from scanning raw_output for the declare_complete
    MCP tool marker, independently of artifact presence. required_artifact_present
    is True only when the artifact file exists on disk, parses as valid JSON,
    and contains a non-empty dict for phases that have a registered required artifact.
    Phases without a registered required artifact always return
    required_artifact_present=False so OpenCode agents cannot implicitly succeed
    — they must call declare_complete explicitly.

    Args:
        workspace: Workspace root path.
        raw_output: Raw NDJSON lines from agent stdout for explicit-completion detection.
        required_artifact: Policy-derived artifact metadata.

    Returns:
        CompletionSignals reflecting current artifact state and explicit completion.
    """
    explicit = extract_explicit_completion(raw_output or [])
    ra = required_artifact
    sentinel_present = (
        _check_completion_sentinel(workspace, run_id) if run_id is not None else False
    )
    if ra is None:
        return CompletionSignals(
            explicit_complete=explicit,
            required_artifact_present=False,
            artifact_types=(),
            completion_sentinel_present=sentinel_present,
        )
    artifact_path = workspace / ra.json_path
    # A run-scoped submission receipt is authoritative proof the artifact was
    # persisted, independent of where it landed; fall back to the on-disk path
    # check only when no receipt is available (e.g. run_id not threaded).
    present = (
        is_artifact_submitted(workspace, run_id, ra.artifact_type)
        if (run_id is not None)
        else False
    )
    present = present or _artifact_is_schema_valid(artifact_path)
    optional = not ra.artifact_required
    sentinel_present = (
        _check_completion_sentinel(workspace, run_id) if run_id is not None else False
    )
    return CompletionSignals(
        explicit_complete=explicit,
        required_artifact_present=present,
        artifact_types=(ra.artifact_type,) if present else (),
        artifact_optional=optional,
        completion_sentinel_present=sentinel_present,
    )


__all__ = [
    "CompletionSignals",
    "evaluate_completion",
    "extract_explicit_completion",
    "is_artifact_submitted",
]
