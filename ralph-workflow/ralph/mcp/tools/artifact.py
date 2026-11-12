from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts._artifact_persistence import (
    DEFAULT_ARTIFACT_PERSISTENCE,
    ArtifactPersistence,
)
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.mcp.tools.coordination import CoordinationSessionLike, WorkspaceLike


def _noop_now_iso() -> str:
    return DEFAULT_ARTIFACT_PERSISTENCE.now_iso()


@dataclass(frozen=True)
class ArtifactHandlerDeps:
    backend: FileBackend = DEFAULT_FILE_BACKEND
    now_iso: Callable[[], str] = _noop_now_iso
    history_enabled: bool = False
    receipt_secret: str | None = None

    @property
    def artifact_persistence(self) -> ArtifactPersistence:
        return ArtifactPersistence(backend=self.backend, now_iso=self.now_iso)


DEFAULT_ARTIFACT_HANDLER_DEPS = ArtifactHandlerDeps()

KNOWN_ARTIFACT_TYPES: frozenset[str] = frozenset(
    {
        "plan",
        "planning_analysis_decision",
        "development_analysis_decision",
        "review_analysis_decision",
        "policy_remediation_analysis_decision",
        "development_result",
        "product_spec",
        "issues",
        "fix_result",
        "design_verdict",
        "smoke_test_result",
        "run_time_report",
        "commit_review",
        "commit_message",
    }
)


def _artifact_dir(workspace: WorkspaceLike) -> Path:
    return Path(workspace.absolute_path(".agent/artifacts"))


def _workspace_root(workspace: WorkspaceLike) -> Path:
    return Path(workspace.absolute_path("."))


def _resolve_artifact_dir(session: CoordinationSessionLike, workspace: WorkspaceLike) -> Path:
    worker = cast("Path | None", getattr(session, "worker_artifact_dir", None))
    return worker if worker is not None else _artifact_dir(workspace)


def _session_run_id(session: CoordinationSessionLike) -> str | None:
    value = cast("object", getattr(session, "run_id", None))
    return value if isinstance(value, str) and value else None


def _session_drain(session: CoordinationSessionLike) -> str | None:
    try:
        attributes = cast("dict[str, object]", vars(session))
    except TypeError:
        return None
    value = attributes.get("drain")
    return value if isinstance(value, str) else None


def _resolve_history_enabled(artifact_type: str, workspace_root: Path, drain: str | None) -> bool:
    del artifact_type
    try:
        policy = load_policy(workspace_root / ".agent")
        return any(
            phase.drain == drain
            and phase.artifact_history is not None
            and phase.artifact_history.enabled
            for phase in policy.pipeline.phases.values()
        )
    except Exception:
        return False


__all__ = [
    "DEFAULT_ARTIFACT_HANDLER_DEPS",
    "KNOWN_ARTIFACT_TYPES",
    "ArtifactHandlerDeps",
    "_resolve_artifact_dir",
    "_resolve_history_enabled",
    "_session_drain",
    "_session_run_id",
    "_workspace_root",
]
