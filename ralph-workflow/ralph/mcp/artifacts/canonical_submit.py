"""Canonical persistence for markdown artifact documents."""

from __future__ import annotations

from dataclasses import dataclass, replace
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.completion_receipts import (
    artifact_receipt_present,
    delete_artifact_receipt,
    write_artifact_receipt,
)
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.handoffs import handoff_path_for_artifact
from ralph.mcp.artifacts.history import (
    history_dir_for_artifact,
    rebuild_history_index,
    snapshot_current_artifact,
)
from ralph.mcp.artifacts.idempotent_write import atomic_write_text_if_changed
from ralph.mcp.artifacts.markdown import MarkdownArtifactError, parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec, registered_specs

if TYPE_CHECKING:
    from ralph.mcp.tools.artifact import ArtifactHandlerDeps


@dataclass(frozen=True)
class SubmitResult:
    """Locations produced by one canonical markdown submission."""

    artifact_path: Path | None
    receipt_path: Path | None
    handoff_path: Path | None
    artifact_type: str
    run_id: str | None


def _artifact_dir(workspace_root: Path) -> Path:
    return workspace_root / ".agent" / "artifacts"


def _capture_file_state(
    backend: FileBackend,
    path: Path,
) -> tuple[bool, str]:
    """Capture enough state to restore one markdown file after a failed submit."""
    if not backend.exists(path):
        return False, ""
    return True, backend.read_text(path, encoding="utf-8")


def _restore_file_state(
    backend: FileBackend,
    path: Path,
    state: tuple[bool, str],
) -> None:
    """Restore a captured file or remove a file created by the failed submit."""
    existed, content = state
    if existed:
        atomic_write_text_if_changed(
            backend,
            path,
            content,
            tmp_path=path.with_suffix(".md.tmp"),
            encoding="utf-8",
        )
        return
    backend.unlink(path, missing_ok=True)


def _rollback_submission(
    *,
    workspace_root: Path,
    backend: FileBackend,
    artifact_path: Path,
    artifact_state: tuple[bool, str],
    handoff_path: Path | None,
    handoff_state: tuple[bool, str] | None,
    history_paths: list[Path],
    history_dir: Path | None,
    history_paths_before: frozenset[Path],
    artifact_dir: Path,
    artifact_type: str,
    run_id: str | None,
    restorable_receipt_preexisting: bool,
) -> None:
    """Restore canonical files and remove history created by a failed submit."""
    rollback_errors: list[Exception] = []
    if handoff_path is not None and handoff_state is not None:
        try:
            _restore_file_state(backend, handoff_path, handoff_state)
        except Exception as exc:
            rollback_errors.append(exc)
    try:
        _restore_file_state(backend, artifact_path, artifact_state)
    except Exception as exc:
        rollback_errors.append(exc)
    rollback_history_paths = set(history_paths)
    if history_dir is not None:
        try:
            rollback_history_paths.update(
                set(backend.glob(history_dir, "*.md")) - history_paths_before
            )
        except Exception as exc:
            rollback_errors.append(exc)
    for history_path in sorted(rollback_history_paths):
        try:
            backend.unlink(history_path, missing_ok=True)
        except Exception as exc:
            rollback_errors.append(exc)
    if rollback_history_paths:
        try:
            rebuild_history_index(
                artifact_dir,
                artifact_type,
                backend=backend,
            )
        except Exception as exc:
            rollback_errors.append(exc)
    if run_id is not None and not restorable_receipt_preexisting:
        try:
            delete_artifact_receipt(
                workspace_root,
                run_id,
                artifact_type,
                backend=backend,
            )
        except Exception as exc:
            rollback_errors.append(exc)
    if rollback_errors:
        raise ExceptionGroup(
            "Canonical artifact submission rollback was incomplete",
            rollback_errors,
        )


def submit_artifact_canonical(
    workspace_root: Path,
    artifact_type: str,
    parsed_content: dict[str, object],
    *,
    markdown: str | None = None,
    deps: ArtifactHandlerDeps | None = None,
    run_id: str | None = None,
    artifact_dir: Path | None = None,
    handoff_dir: Path | None = None,
) -> SubmitResult:
    """Persist validated markdown, handoff, and receipt as one logical transaction.

    ``parsed_content`` is retained only for callers that need to validate before
    persistence; the stored artifact is always ``.md`` and never a JSON envelope.
    If receipt persistence fails, canonical files and any history snapshot are
    restored to their pre-submit state before the persistence error propagates.
    Phase completion remains a separate explicit ``declare_complete`` operation.
    """
    del parsed_content
    if markdown is None:
        raise ValueError("markdown source is required for migrated artifacts")
    if deps is None:
        deps = cast(
            "ArtifactHandlerDeps",
            import_module("ralph.mcp.tools.artifact").DEFAULT_ARTIFACT_HANDLER_DEPS,
        )
    backend = deps.backend
    directory = artifact_dir or _artifact_dir(workspace_root)
    artifact_path = directory / f"{artifact_type}.md"
    handoff_relative = handoff_path_for_artifact(artifact_type)
    handoff_path = (
        (
            handoff_dir / Path(handoff_relative).name
            if handoff_dir is not None
            else workspace_root / handoff_relative
        )
        if handoff_relative is not None
        else None
    )
    if handoff_path == artifact_path:
        handoff_path = None

    artifact_state = _capture_file_state(backend, artifact_path)
    handoff_state = _capture_file_state(backend, handoff_path) if handoff_path is not None else None
    history_paths: list[Path] = []
    history_enabled = deps.history_enabled and handoff_dir is None and artifact_state[0]
    history_dir = history_dir_for_artifact(directory, artifact_type) if history_enabled else None
    history_paths_before = (
        frozenset(backend.glob(history_dir, "*.md"))
        if history_dir is not None and backend.exists(history_dir)
        else frozenset()
    )
    receipt_path: Path | None = None
    restorable_receipt_preexisting = artifact_state[0] and (
        artifact_receipt_present(
            workspace_root,
            run_id,
            artifact_type,
            backend=backend,
            receipt_secret=deps.receipt_secret,
        )
        if run_id is not None
        else False
    )
    try:
        backend.mkdir(directory, parents=True, exist_ok=True)
        # Worker-local submissions carry their own handoff directory and are
        # replaced between isolated attempts. Shared artifact history would look
        # up the coordinator handoff and leak it into the worker namespace.
        if history_enabled:
            history_paths = snapshot_current_artifact(
                directory,
                workspace_root,
                artifact_type,
                backend=backend,
                now_iso=deps.now_iso,
            )
        atomic_write_text_if_changed(
            backend,
            artifact_path,
            markdown,
            tmp_path=artifact_path.with_suffix(".md.tmp"),
            encoding="utf-8",
        )
        if backend.read_text(artifact_path, encoding="utf-8") != markdown:
            raise OSError(f"canonical artifact write was corrupt: {artifact_path}")

        if handoff_path is not None:
            backend.mkdir(handoff_path.parent, parents=True, exist_ok=True)
            atomic_write_text_if_changed(
                backend,
                handoff_path,
                markdown,
                tmp_path=handoff_path.with_suffix(".md.tmp"),
                encoding="utf-8",
            )
            if backend.read_text(handoff_path, encoding="utf-8") != markdown:
                raise OSError(f"canonical handoff write was corrupt: {handoff_path}")

        if run_id is not None:
            receipt_path = write_artifact_receipt(
                workspace_root,
                run_id,
                artifact_type,
                backend=backend,
                receipt_secret=deps.receipt_secret,
            )
    except Exception as submission_error:
        try:
            _rollback_submission(
                workspace_root=workspace_root,
                backend=backend,
                artifact_path=artifact_path,
                artifact_state=artifact_state,
                handoff_path=handoff_path,
                handoff_state=handoff_state,
                history_paths=history_paths,
                history_dir=history_dir,
                history_paths_before=history_paths_before,
                artifact_dir=directory,
                artifact_type=artifact_type,
                run_id=run_id,
                restorable_receipt_preexisting=restorable_receipt_preexisting,
            )
        except Exception as rollback_error:
            raise ExceptionGroup(
                "Canonical artifact submission failed and rollback was incomplete",
                [submission_error, rollback_error],
            ) from None
        raise

    return SubmitResult(
        artifact_path=artifact_path,
        receipt_path=receipt_path,
        handoff_path=handoff_path,
        artifact_type=artifact_type,
        run_id=run_id,
    )


def _registered_markdown_types() -> tuple[str, ...]:
    """Return the artifact types with a registered markdown spec."""
    import_module("ralph.mcp.artifacts.markdown.specs")
    return tuple(spec.artifact_type for spec in registered_specs())


def _fallback_path(workspace_root: Path, artifact_type: str) -> Path:
    return workspace_root / ".agent" / "tmp" / f"{artifact_type}.md"


def _clear_fallback_artifacts(
    workspace_root: Path,
    run_id: str,
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    fallback_dir: Path | None = None,
) -> None:
    """Clear stale Markdown fallback files from a newly started run."""
    del run_id
    tmp = fallback_dir or workspace_root / ".agent" / "tmp"
    if not backend.exists(tmp):
        return
    for artifact_type in _registered_markdown_types():
        backend.unlink(tmp / f"{artifact_type}.md", missing_ok=True)


def _clear_worker_artifacts(
    workspace_root: Path,
    run_id: str,
    *,
    worker_namespace: Path,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    """Clear stale canonical, handoff, and fallback documents for one worker."""
    artifact_dir = worker_namespace / "artifacts"
    handoff_dir = worker_namespace / "handoffs"
    _clear_fallback_artifacts(
        workspace_root,
        run_id,
        backend=backend,
        fallback_dir=worker_namespace / "tmp",
    )
    for artifact_type in _registered_markdown_types():
        backend.unlink(artifact_dir / f"{artifact_type}.md", missing_ok=True)
        relative_handoff = handoff_path_for_artifact(artifact_type)
        if relative_handoff is not None:
            backend.unlink(
                handoff_dir / Path(relative_handoff).name,
                missing_ok=True,
            )


def promote_fallback_artifact(
    workspace_root: Path,
    artifact_type: str,
    *,
    deps: ArtifactHandlerDeps | None = None,
    run_id: str | None = None,
    receipt_secret: str | None = None,
    fallback_path: Path | None = None,
    artifact_dir: Path | None = None,
    handoff_dir: Path | None = None,
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
    fallback = fallback_path or _fallback_path(workspace_root, artifact_type)
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
        artifact_dir=artifact_dir,
        handoff_dir=handoff_dir,
    )
    backend.unlink(fallback, missing_ok=True)
    return result


__all__ = [
    "SubmitResult",
    "_clear_fallback_artifacts",
    "_clear_worker_artifacts",
    "promote_fallback_artifact",
    "submit_artifact_canonical",
]
