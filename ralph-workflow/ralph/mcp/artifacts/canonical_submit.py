"""Canonical persistence for markdown artifact documents."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from importlib import import_module
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.completion_receipts import write_artifact_receipt
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.handoffs import handoff_path_for_artifact
from ralph.mcp.artifacts.history import snapshot_current_artifact
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.mcp.artifacts.markdown import MarkdownArtifactError, parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec, registered_specs
from ralph.mcp.artifacts.state_db import RunStateDB
from ralph.mcp.tools.coordination import COMPLETION_SENTINEL_RELPATHFMT

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.tools.artifact import ArtifactHandlerDeps


@dataclass(frozen=True)
class SubmitResult:
    """Locations produced by one canonical markdown submission."""

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


def submit_artifact_canonical(
    workspace_root: Path,
    artifact_type: str,
    parsed_content: dict[str, object],
    *,
    markdown: str | None = None,
    deps: ArtifactHandlerDeps | None = None,
    run_id: str | None = None,
    artifact_dir: Path | None = None,
    name: str | None = None,
    overwrite: bool = True,
    metadata: dict[str, object] | None = None,
) -> SubmitResult:
    """Write the validated markdown source of truth and receipt atomically by order.

    ``parsed_content`` is retained only for callers that need to validate before
    persistence; the stored artifact is always ``.md`` and never a JSON envelope.
    """
    del parsed_content, overwrite, metadata
    if markdown is None:
        raise ValueError("markdown source is required for migrated artifacts")
    if deps is None:
        deps = cast(
            "ArtifactHandlerDeps",
            import_module("ralph.mcp.tools.artifact").DEFAULT_ARTIFACT_HANDLER_DEPS,
        )
    backend = deps.backend
    directory = artifact_dir or _artifact_dir(workspace_root)
    stem = name or artifact_type
    artifact_path = directory / f"{stem}.md"
    backend.mkdir(directory, parents=True, exist_ok=True)
    if deps.history_enabled and backend.exists(artifact_path):
        snapshot_current_artifact(directory, workspace_root, artifact_type, backend=backend, now_iso=deps.now_iso)
    write_text_if_changed(backend, artifact_path, markdown, encoding="utf-8")

    handoff_relative = handoff_path_for_artifact(artifact_type)
    handoff_path = workspace_root / handoff_relative if handoff_relative is not None else None
    if handoff_path is not None and handoff_path != artifact_path:
        backend.mkdir(handoff_path.parent, parents=True, exist_ok=True)
        write_text_if_changed(backend, handoff_path, markdown, encoding="utf-8")

    receipt_path: Path | None = None
    sentinel_path: Path | None = None
    if run_id is not None:
        write_artifact_receipt(
            workspace_root,
            run_id,
            artifact_type,
            backend=backend,
            receipt_secret=deps.receipt_secret,
        )
        receipt_path = _receipt_path(workspace_root, run_id, artifact_type)
        if artifact_type not in {"plan", "planning_analysis_decision", "development_analysis_decision", "review_analysis_decision"}:
            try:
                state = RunStateDB(workspace_root)
                try:
                    state.upsert_completion_sentinel(run_id, None)
                finally:
                    state.close()
            except (OSError, RuntimeError, sqlite3.Error):
                pass
            sentinel_path = _sentinel_path(workspace_root, run_id)
    return SubmitResult(artifact_path, receipt_path, sentinel_path, handoff_path, artifact_type, run_id)


def _registered_markdown_types() -> tuple[str, ...]:
    """Return the artifact types with a registered markdown spec."""
    import_module("ralph.mcp.artifacts.markdown.specs")
    return tuple(spec.artifact_type for spec in registered_specs())


def _fallback_path(workspace_root: Path, artifact_type: str) -> Path:
    return workspace_root / ".agent" / "tmp" / f"{artifact_type}.md"


def _clear_fallback_artifacts(workspace_root: Path, run_id: str, *, backend: FileBackend = DEFAULT_FILE_BACKEND) -> None:
    """Clear obsolete fallback files (legacy JSON and markdown) from a newly started run."""
    del run_id
    tmp = workspace_root / ".agent" / "tmp"
    if not backend.exists(tmp):
        return
    for path in backend.glob(tmp, "*.json"):
        backend.unlink(path, missing_ok=True)
    for artifact_type in _registered_markdown_types():
        backend.unlink(_fallback_path(workspace_root, artifact_type), missing_ok=True)


def promote_fallback_artifact(
    workspace_root: Path,
    artifact_type: str,
    *,
    deps: ArtifactHandlerDeps | None = None,
    run_id: str | None = None,
    receipt_secret: str | None = None,
) -> SubmitResult | None:
    """Promote an agent-written ``.agent/tmp/<type>.md`` fallback through canonical submit.

    Returns ``None`` when no fallback document exists, the artifact type has
    no registered markdown spec, or the document fails markdown validation —
    an invalid fallback must not stamp a submission receipt.
    """
    import_module("ralph.mcp.artifacts.markdown.specs")
    try:
        spec = get_spec(artifact_type)
    except ValueError:
        return None
    resolved_deps = deps
    if resolved_deps is None:
        resolved_deps = cast(
            "ArtifactHandlerDeps",
            import_module("ralph.mcp.tools.artifact").DEFAULT_ARTIFACT_HANDLER_DEPS,
        )
    if receipt_secret is not None:
        resolved_deps = replace(resolved_deps, receipt_secret=receipt_secret)
    backend = resolved_deps.backend
    fallback = _fallback_path(workspace_root, artifact_type)
    if not backend.exists(fallback):
        return None
    try:
        markdown = backend.read_text(fallback, encoding="utf-8")
    except OSError:
        return None
    try:
        parsed_content, diagnostics = parse_and_validate(markdown, spec)
    except (ValueError, MarkdownArtifactError):
        return None
    if any(diagnostic.severity == "error" for diagnostic in diagnostics):
        return None
    result = submit_artifact_canonical(
        workspace_root=workspace_root,
        artifact_type=artifact_type,
        parsed_content=dict(parsed_content),
        markdown=markdown,
        deps=resolved_deps,
        run_id=run_id,
    )
    backend.unlink(fallback, missing_ok=True)
    return result


__all__ = ["SubmitResult", "_clear_fallback_artifacts", "promote_fallback_artifact", "submit_artifact_canonical"]
