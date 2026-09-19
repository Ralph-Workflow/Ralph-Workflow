"""Persistence for markdown-artifact validation retry hints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ralph.mcp.artifacts.idempotent_write import write_text_if_changed
from ralph.mcp.tools.artifact import (
    DEFAULT_ARTIFACT_HANDLER_DEPS,
    ArtifactHandlerDeps,
    _session_drain,
    _workspace_root,
)
from ralph.phases.required_artifacts import build_validation_retry_hint, retry_hint_path

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.artifacts.markdown import Diagnostic
    from ralph.mcp.tools.coordination import CoordinationSessionLike, WorkspaceLike


@runtime_checkable
class _WorkerNamespaceSession(Protocol):
    worker_namespace: Path | None


@runtime_checkable
class _WorkerArtifactDirSession(Protocol):
    worker_artifact_dir: Path | None


@runtime_checkable
class _PhaseScopedSession(Protocol):
    phase: str


def _worker_namespace(session: object) -> Path | None:
    if isinstance(session, _WorkerNamespaceSession) and session.worker_namespace is not None:
        return session.worker_namespace
    if isinstance(session, _WorkerArtifactDirSession) and session.worker_artifact_dir is not None:
        return session.worker_artifact_dir.parent
    return None


def validation_retry_hint_file(
    session: CoordinationSessionLike, workspace: WorkspaceLike
) -> Path | None:
    """Resolve the drain-keyed retry-hint path for coordinator or worker scope."""
    drain = _session_drain(session)
    if drain is None:
        return None
    worker_namespace = _worker_namespace(session)
    if worker_namespace is not None:
        return worker_namespace / "tmp" / f"last_retry_error_{drain}.txt"
    return _workspace_root(workspace) / retry_hint_path(drain)


def persist_validation_retry_hint(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    artifact_type: str,
    diagnostics: list[Diagnostic],
    deps: ArtifactHandlerDeps | None,
) -> None:
    """Persist exact validator errors for injection into the next retry prompt."""
    backend = (deps or DEFAULT_ARTIFACT_HANDLER_DEPS).backend
    path = validation_retry_hint_file(session, workspace)
    if path is None:
        return
    backend.mkdir(path.parent, parents=True, exist_ok=True)
    prior_hint = backend.read_text(path) if backend.exists(path) else ""
    write_text_if_changed(
        backend,
        path,
        build_validation_retry_hint(artifact_type, diagnostics, prior_hint=prior_hint),
    )


def clear_validation_retry_hint(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    deps: ArtifactHandlerDeps | None,
) -> None:
    """Clear canonical and legacy validation context after successful submission."""
    backend = (deps or DEFAULT_ARTIFACT_HANDLER_DEPS).backend
    path = validation_retry_hint_file(session, workspace)
    if path is None:
        return
    backend.unlink(path, missing_ok=True)
    phase_name = session.phase if isinstance(session, _PhaseScopedSession) else None
    drain = _session_drain(session)
    if not isinstance(phase_name, str) or drain is None or phase_name == drain:
        return
    worker_namespace = _worker_namespace(session)
    legacy_path = (
        worker_namespace / "tmp" / f"last_retry_error_{phase_name}.txt"
        if worker_namespace is not None
        else _workspace_root(workspace) / retry_hint_path(phase_name)
    )
    backend.unlink(legacy_path, missing_ok=True)
