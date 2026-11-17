from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.completion_receipts import (
    artifact_receipt_present,
    delete_artifact_receipt,
    read_artifact_submission_snapshot,
    write_artifact_receipt,
)
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.handoffs import handoff_path_for_artifact
from ralph.mcp.artifacts.history import (
    history_dir_for_artifact,
    rebuild_history_index,
    snapshot_current_artifact,
    submission_snapshot_path,
    validate_submission_snapshot_components,
    write_submission_snapshot,
)
from ralph.mcp.artifacts.idempotent_write import atomic_write_text_if_changed
from ralph.mcp.artifacts.markdown import MarkdownArtifactError, parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec, registered_specs

_restore_artifact_receipt = write_artifact_receipt

if TYPE_CHECKING:
    from ralph.mcp.tools.artifact import ArtifactHandlerDeps


def _normalize_commit_submission(
    artifact_type: str, markdown: str, workspace_root: Path
) -> tuple[str, dict[str, object] | None]:
    if artifact_type != "commit_message":
        return markdown, None
    from ralph.mcp.tools.commit_normalization import normalize_commit_submission

    return normalize_commit_submission(artifact_type, markdown, workspace_root)


def _default_artifact_handler_deps() -> ArtifactHandlerDeps:
    from ralph.mcp.tools.artifact import DEFAULT_ARTIFACT_HANDLER_DEPS

    return DEFAULT_ARTIFACT_HANDLER_DEPS


@dataclass(frozen=True)
class SubmitResult:
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
    if not backend.exists(path):
        return False, ""
    return True, backend.read_text(path, encoding="utf-8")


_ROLLBACK_EXCEPTIONS = (
    OSError,
    RuntimeError,
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    ExceptionGroup,
)


def _assert_canonical_write_matches(backend: FileBackend, path: Path, expected: str) -> None:
    if backend.read_text(path, encoding="utf-8") != expected:
        raise OSError(f"canonical artifact write was corrupt: {path}")


def _restore_file_state(
    backend: FileBackend,
    path: Path,
    state: tuple[bool, str],
) -> None:
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


def _restore_receipt_state(
    workspace_root: Path,
    run_id: str,
    artifact_type: str,
    previous_receipt_snapshot: tuple[str, str] | None,
    receipt_secret: str | None,
    *,
    backend: FileBackend,
    preexisting: bool,
) -> None:
    if not preexisting:
        delete_artifact_receipt(workspace_root, run_id, artifact_type, backend=backend)
        return
    previous_path, previous_digest = (
        previous_receipt_snapshot if previous_receipt_snapshot is not None else (None, None)
    )
    _restore_artifact_receipt(
        workspace_root,
        run_id,
        artifact_type,
        backend=backend,
        receipt_secret=receipt_secret,
        submission_snapshot_path=previous_path,
        submission_snapshot_digest=previous_digest,
    )


def _write_handoff(
    backend: FileBackend,
    handoff_path: Path | None,
    markdown: str,
    artifact_type: str,
) -> None:
    if handoff_path is None:
        return
    if not backend.exists(handoff_path.parent):
        backend.mkdir(handoff_path.parent, parents=True, exist_ok=True)
    atomic_write_text_if_changed(
        backend,
        handoff_path,
        markdown,
        tmp_path=handoff_path.with_suffix(".md.tmp"),
        encoding="utf-8",
        sync_directory=artifact_type == "plan",
    )
    if backend.read_text(handoff_path, encoding="utf-8") != markdown:
        raise OSError(f"canonical handoff write was corrupt: {handoff_path}")


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
    previous_receipt_snapshot: tuple[str, str] | None,
    receipt_secret: str | None,
    submission_snapshot_path_value: Path | None,
    submission_snapshot_state: tuple[bool, str] | None,
) -> None:
    rollback_errors: list[Exception] = []
    if handoff_path is not None and handoff_state is not None:
        try:
            _restore_file_state(backend, handoff_path, handoff_state)
        except _ROLLBACK_EXCEPTIONS as exc:
            rollback_errors.append(exc)
    if submission_snapshot_path_value is not None and submission_snapshot_state is not None:
        try:
            _restore_file_state(
                backend,
                submission_snapshot_path_value,
                submission_snapshot_state,
            )
        except _ROLLBACK_EXCEPTIONS as exc:
            rollback_errors.append(exc)
    try:
        _restore_file_state(backend, artifact_path, artifact_state)
    except _ROLLBACK_EXCEPTIONS as exc:
        rollback_errors.append(exc)
    rollback_history_paths = set(history_paths)
    if history_dir is not None:
        try:
            rollback_history_paths.update(
                set(backend.glob(history_dir, "*.md")) - history_paths_before
            )
        except _ROLLBACK_EXCEPTIONS as exc:
            rollback_errors.append(exc)
    for history_path in sorted(rollback_history_paths):
        try:
            backend.unlink(history_path, missing_ok=True)
        except _ROLLBACK_EXCEPTIONS as exc:
            rollback_errors.append(exc)
    if rollback_history_paths:
        try:
            rebuild_history_index(
                artifact_dir,
                artifact_type,
                backend=backend,
            )
        except _ROLLBACK_EXCEPTIONS as exc:
            rollback_errors.append(exc)
    if run_id is not None:
        try:
            _restore_receipt_state(
                workspace_root,
                run_id,
                artifact_type,
                previous_receipt_snapshot,
                receipt_secret,
                backend=backend,
                preexisting=restorable_receipt_preexisting,
            )
        except _ROLLBACK_EXCEPTIONS as exc:
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
    normalization_audit: dict[str, object] | None = None,
) -> SubmitResult:
    del parsed_content
    if markdown is None:
        raise ValueError("markdown source is required for migrated artifacts")
    if run_id is not None:
        validate_submission_snapshot_components(run_id, artifact_type)
    if normalization_audit is None:
        markdown, normalization_audit = _normalize_commit_submission(
            artifact_type, markdown, workspace_root
        )
    if deps is None:
        deps = _default_artifact_handler_deps()
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
    artifact_changed = not artifact_state[0] or artifact_state[1] != markdown
    history_enabled = deps.history_enabled and handoff_dir is None and artifact_changed
    history_dir = history_dir_for_artifact(directory, artifact_type) if history_enabled else None
    history_paths_before = (
        frozenset(backend.glob(history_dir, "*.md"))
        if history_dir is not None and backend.exists(history_dir)
        else frozenset()
    )
    receipt_path: Path | None = None
    snapshot_path: Path | None = None
    snapshot_state: tuple[bool, str] | None = None
    snapshot_digest: str | None = None
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
    previous_receipt_snapshot = None
    if run_id is not None and restorable_receipt_preexisting:
        previous_receipt_snapshot = read_artifact_submission_snapshot(
            workspace_root,
            run_id,
            artifact_type,
            backend=backend,
            receipt_secret=deps.receipt_secret,
        )
    try:
        if not backend.exists(directory):
            backend.mkdir(directory, parents=True, exist_ok=True)
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
            sync_directory=artifact_type == "plan",
        )
        _assert_canonical_write_matches(backend, artifact_path, markdown)

        _write_handoff(backend, handoff_path, markdown, artifact_type)

        if run_id is not None and artifact_type == "plan":
            snapshot_path = submission_snapshot_path(workspace_root, run_id, artifact_type)
            snapshot_state = _capture_file_state(backend, snapshot_path)
            snapshot_path, snapshot_digest = write_submission_snapshot(
                workspace_root,
                run_id,
                artifact_type,
                markdown,
                backend=backend,
            )

        if run_id is not None:
            receipt_path = write_artifact_receipt(
                workspace_root,
                run_id,
                artifact_type,
                backend=backend,
                receipt_secret=deps.receipt_secret,
                normalization_audit=normalization_audit,
                submission_snapshot_path=(
                    snapshot_path.relative_to(workspace_root).as_posix()
                    if snapshot_path is not None
                    else None
                ),
                submission_snapshot_digest=snapshot_digest,
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
                previous_receipt_snapshot=previous_receipt_snapshot,
                receipt_secret=deps.receipt_secret,
                submission_snapshot_path_value=snapshot_path,
                submission_snapshot_state=snapshot_state,
            )
        except _ROLLBACK_EXCEPTIONS as rollback_error:
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
    from ralph.mcp.artifacts.markdown import specs as _markdown_specs
    _ = _markdown_specs
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
    from ralph.mcp.artifacts.markdown import specs as _markdown_specs
    _ = _markdown_specs
    try:
        spec = get_spec(artifact_type)
    except ValueError:
        return None
    resolved_deps = deps
    if resolved_deps is None:
        resolved_deps = _default_artifact_handler_deps()
    if receipt_secret is not None:
        resolved_deps = replace(resolved_deps, receipt_secret=receipt_secret)
    backend = resolved_deps.backend
    fallback = fallback_path or _fallback_path(workspace_root, artifact_type)
    if not backend.exists(fallback):
        return None
    try:
        markdown = backend.read_text(fallback, encoding="utf-8")
        markdown, normalization_audit = _normalize_commit_submission(
            artifact_type, markdown, workspace_root
        )
    except (OSError, ValueError):
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
        normalization_audit=normalization_audit,
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
