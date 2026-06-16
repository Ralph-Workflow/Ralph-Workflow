"""Canonical artifact submission entry point.

This module is the single public writer of run-scoped completion receipts and
completion sentinels for canonical artifact types. Every artifact submission
that needs to satisfy the completion gate must route through
:func:`submit_artifact_canonical` so the receipt, sentinel, artifact file, and
Markdown handoff are written atomically (or rolled back together).
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.handoffs import handoff_path_for_artifact
from ralph.mcp.tools.coordination import COMPLETION_SENTINEL_RELPATHFMT, InvalidParamsError

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.artifacts.file_backend import FileBackend
    from ralph.mcp.tools._submit_op import SubmitOp
    from ralph.mcp.tools.artifact import ArtifactHandlerDeps


class _ToolsArtifactModule(Protocol):
    DEFAULT_ARTIFACT_HANDLER_DEPS: ArtifactHandlerDeps

    def _submit_ops_for_artifact_with_options(
        self,
        artifact_type: str,
        workspace_root: Path,
        artifact_dir: Path,
        parsed_content: dict[str, object],
        *,
        deps: ArtifactHandlerDeps,
        run_id: str | None = ...,
        name: str | None = ...,
        overwrite: bool = ...,
        metadata: dict[str, object] | None = ...,
    ) -> list[SubmitOp]: ...

    def execute_ops_with_rollback(self, ops: list[SubmitOp]) -> None: ...

    def _normalize_artifact_payload(
        self,
        artifact_type: str,
        parsed_content: dict[str, object],
        *,
        workspace_root: Path | None = ...,
        backend: object = ...,
    ) -> dict[str, object]: ...


@dataclass(frozen=True)
class SubmitResult:
    """Paths written by a canonical artifact submission.

    Attributes:
        artifact_path: Path to the canonical artifact JSON file, if written.
        receipt_path: Path to the run-scoped receipt, if written.
        sentinel_path: Path to the completion sentinel, if written for a
            single-shot artifact type.
        handoff_path: Path to the Markdown handoff, if one is configured for
            the artifact type.
        artifact_type: The canonical artifact type that was submitted.
        run_id: The run id used as the receipt/sentinel key.
    """

    artifact_path: Path | None
    receipt_path: Path | None
    sentinel_path: Path | None
    handoff_path: Path | None
    artifact_type: str
    run_id: str | None


def _artifact_dir(workspace_root: Path) -> Path:
    return workspace_root / ".agent" / "artifacts"


def _receipt_path(workspace_root: Path, run_id: str, artifact_type: str) -> Path:
    return workspace_root / ".agent" / "receipts" / run_id / f"{artifact_type}.json"


def _sentinel_path(workspace_root: Path, run_id: str) -> Path:
    return workspace_root / COMPLETION_SENTINEL_RELPATHFMT.format(run_id=run_id)


def _tools_artifact() -> _ToolsArtifactModule:
    """Return the ``ralph.mcp.tools.artifact`` module lazily to avoid cycles."""
    return cast("_ToolsArtifactModule", importlib.import_module("ralph.mcp.tools.artifact"))


def _clear_fallback_artifacts(
    workspace_root: Path,
    run_id: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    """Clear fallback artifacts that could be promoted for this run.

    Prevents a fresh run from inheriting stale fallback artifacts from a
    previous run. Removes .agent/tmp/<artifact_type>.json files, which are
    always fallback files written by agents.

    Note: This does NOT clear .agent/artifacts/<artifact_type>.json, which is
    the canonical artifact location and is managed separately by receipt-based
    validation.

    Args:
        workspace_root: Root of the workspace.
        run_id: The run ID being cleared (used only for documentation; tmp
            directory is shared across runs).
        backend: File backend for I/O operations.
    """
    tmp_dir = workspace_root / ".agent" / "tmp"
    if backend.exists(tmp_dir):
        for path in backend.glob(tmp_dir, "*.json"):
            backend.unlink(path, missing_ok=True)


def _read_fallback_payload(path: Path, backend: FileBackend) -> dict[str, object] | None:
    """Parse a fallback file, tolerating both bare payload and outer envelope."""
    try:
        raw = backend.read_text(path, encoding="utf-8")
        parsed = cast("object", json.loads(raw))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(parsed, dict):
        return None

    content = parsed.get("content")
    if isinstance(content, dict):
        return cast("dict[str, object]", content)
    return cast("dict[str, object]", parsed)


def submit_artifact_canonical(
    workspace_root: Path,
    artifact_type: str,
    parsed_content: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
    run_id: str | None = None,
    artifact_dir: Path | None = None,
    name: str | None = None,
    overwrite: bool = True,
    metadata: dict[str, object] | None = None,
) -> SubmitResult:
    """Submit an artifact through the canonical, receipt-stamping path.

    The submission is atomic: the artifact file, run-scoped receipt, single-shot
    completion sentinel, and Markdown handoff are written inside a single
    rollback-protected operation sequence. If any step fails, all completed steps
    are undone.

    Args:
        workspace_root: Root of the workspace where artifacts/receipts live.
        artifact_type: Canonical artifact type to submit.
        parsed_content: Normalized artifact payload dictionary.
        deps: Injectable dependencies; defaults to ``DEFAULT_ARTIFACT_HANDLER_DEPS``.
        run_id: Run identifier used as the receipt/sentinel key.
        artifact_dir: Directory for the artifact JSON file; defaults to
            ``workspace_root / '.agent' / 'artifacts'``.
        name: Optional artifact filename stem; defaults to ``artifact_type``.
        overwrite: Whether to overwrite an existing artifact file.
        metadata: Optional metadata dictionary for the artifact envelope.

    Returns:
        A frozen :class:`SubmitResult` describing the files that were written.
    """
    tools_artifact = _tools_artifact()
    resolved_deps = deps or tools_artifact.DEFAULT_ARTIFACT_HANDLER_DEPS
    resolved_artifact_dir = (
        artifact_dir if artifact_dir is not None else _artifact_dir(workspace_root)
    )

    resolved_backend = cast("object", resolved_deps.backend)
    parsed_content = tools_artifact._normalize_artifact_payload(
        artifact_type,
        parsed_content,
        workspace_root=workspace_root,
        backend=resolved_backend,
    )

    ops = tools_artifact._submit_ops_for_artifact_with_options(
        artifact_type,
        workspace_root,
        resolved_artifact_dir,
        parsed_content,
        deps=resolved_deps,
        run_id=run_id,
        name=name,
        overwrite=overwrite,
        metadata=metadata,
    )
    tools_artifact.execute_ops_with_rollback(ops)

    backend = resolved_deps.backend
    candidate_artifact = resolved_artifact_dir / f"{name or artifact_type}.json"
    artifact_path: Path | None = candidate_artifact if backend.exists(candidate_artifact) else None

    receipt_path: Path | None = None
    if run_id is not None:
        candidate_receipt = _receipt_path(workspace_root, run_id, artifact_type)
        receipt_path = candidate_receipt if backend.exists(candidate_receipt) else None

    sentinel_path: Path | None = None
    if run_id is not None:
        candidate_sentinel = _sentinel_path(workspace_root, run_id)
        if backend.exists(candidate_sentinel):
            sentinel_path = candidate_sentinel

    handoff_path: Path | None = None
    handoff_relative = handoff_path_for_artifact(artifact_type)
    if handoff_relative is not None:
        candidate_handoff = workspace_root / handoff_relative
        if backend.exists(candidate_handoff):
            handoff_path = candidate_handoff

    return SubmitResult(
        artifact_path=artifact_path,
        receipt_path=receipt_path,
        sentinel_path=sentinel_path,
        handoff_path=handoff_path,
        artifact_type=artifact_type,
        run_id=run_id,
    )


def promote_fallback_artifact(
    workspace_root: Path,
    artifact_type: str,
    *,
    deps: ArtifactHandlerDeps | None = None,
    run_id: str | None = None,
) -> SubmitResult | None:
    """Promote an agent-written fallback file to a canonical submission.

    Scans ``.agent/tmp/<artifact_type>.json`` then
    ``.agent/artifacts/<artifact_type>.json``. For the first existing file, parse
    it (tolerating both the bare inner payload and the outer ``{name,type,content}``
    envelope) and route it through :func:`submit_artifact_canonical` so a receipt
    is stamped.

    Does NOT promote canonical artifacts from ``.agent/artifacts/`` that already
    have a receipt for ANY run_id (including the current one), preventing stale
    artifacts from previous runs from being promoted to a new receipt.

    Returns:
        The :class:`SubmitResult` from the canonical submit, or ``None`` when no
        fallback file exists or parsing fails.
    """
    tools_artifact = _tools_artifact()
    resolved_deps = deps or tools_artifact.DEFAULT_ARTIFACT_HANDLER_DEPS
    backend = resolved_deps.backend

    tmp_fallback = workspace_root / ".agent" / "tmp" / f"{artifact_type}.json"
    artifact_fallback = _artifact_dir(workspace_root) / f"{artifact_type}.json"

    for path in (tmp_fallback, artifact_fallback):
        if not backend.exists(path):
            continue

        # For canonical artifacts (not tmp files), check if this is a stale
        # artifact from a previous run. If a receipt exists for ANY other run_id
        # (but not the current run), this artifact was already submitted through
        # the canonical path and should not be promoted again. This prevents a
        # fresh run from inheriting stale artifacts from previous runs.
        if path != tmp_fallback and run_id is not None:
            receipts_dir = workspace_root / ".agent" / "receipts"
            if backend.exists(receipts_dir):
                has_other_run_receipt = False
                # Check each run directory by looking for receipt files
                # Use glob pattern to find all receipt files across all run directories
                for receipt_path in backend.glob(receipts_dir, "*/*.json"):
                    # Extract run_id from the path: receipts_dir/run_id/artifact_type.json
                    parts = receipt_path.relative_to(receipts_dir).parts
                    if len(parts) >= len(["run_id", "artifact_type.json"]):
                        receipt_run_id = parts[0]
                        receipt_artifact_type = receipt_path.stem
                        # Check if this is the same artifact type and a different run
                        if receipt_artifact_type == artifact_type and receipt_run_id != run_id:
                            # A receipt exists for a different run - this is stale
                            has_other_run_receipt = True
                            break
                if has_other_run_receipt:
                    return None

        parsed = _read_fallback_payload(path, backend)
        if parsed is None:
            # A malformed file at this location does not preclude a valid
            # fallback at the next location; continue scanning.
            continue
        try:
            return submit_artifact_canonical(
                workspace_root,
                artifact_type,
                parsed,
                deps=resolved_deps,
                run_id=run_id,
            )
        except InvalidParamsError:
            # Schema-invalid fallback content means no promotion; continue
            # scanning in case a valid copy exists at the next location.
            continue

    return None


__all__ = [
    "SubmitResult",
    "_clear_fallback_artifacts",
    "promote_fallback_artifact",
    "submit_artifact_canonical",
]
