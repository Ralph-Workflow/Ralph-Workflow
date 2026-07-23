"""Shared dependencies for markdown artifact submission.

JSON artifact authoring was removed.  The markdown handlers own the public MCP
surface; this module deliberately keeps only the small persistence seam they
share with canonical submission.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts._artifact_persistence import (
    DEFAULT_ARTIFACT_PERSISTENCE,
    ArtifactPersistence,
)
from ralph.mcp.artifacts.commit_message import normalize_commit_message_content
from ralph.mcp.artifacts.development_result import normalize_development_result_content
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.plan import normalize_plan_artifact_content
from ralph.mcp.artifacts.product_spec import normalize_product_spec_content
from ralph.mcp.artifacts.smoke_test_result import normalize_smoke_test_result_content
from ralph.mcp.artifacts.typed_artifacts import (
    normalize_analysis_decision_content,
    normalize_commit_cleanup_content,
    normalize_fix_result_content,
    normalize_issues_content,
)
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.mcp.tools._submit_op import SubmitOp
    from ralph.mcp.tools.coordination import CoordinationSessionLike, WorkspaceLike


def _noop_now_iso() -> str:
    return DEFAULT_ARTIFACT_PERSISTENCE.now_iso()


@dataclass(frozen=True)
class ArtifactHandlerDeps:
    """Injectable filesystem and timestamp dependencies for artifact writes."""

    backend: FileBackend = DEFAULT_FILE_BACKEND
    now_iso: Callable[[], str] = _noop_now_iso
    history_enabled: bool = False
    receipt_secret: str | None = None

    @property
    def artifact_persistence(self) -> ArtifactPersistence:
        """Return the matching persistence bundle for compatibility callers."""
        return ArtifactPersistence(backend=self.backend, now_iso=self.now_iso)


DEFAULT_ARTIFACT_HANDLER_DEPS = ArtifactHandlerDeps()

_KNOWN_ARTIFACT_TYPES = frozenset({
    "plan", "planning_analysis_decision", "development_analysis_decision",
    "review_analysis_decision", "development_result", "product_spec", "issues",
    "fix_result", "smoke_test_result", "commit_cleanup", "commit_message",
})


def execute_ops_with_rollback(ops: list[SubmitOp]) -> None:
    """Run operations atomically enough for the canonical writer."""
    completed: list[SubmitOp] = []
    try:
        for op in ops:
            op.run()
            completed.append(op)
    except Exception:
        for op in reversed(completed):
            with suppress(Exception):
                op.undo()
        raise


def _artifact_dir(workspace: WorkspaceLike) -> Path:
    return Path(workspace.absolute_path(".agent/artifacts"))


def _workspace_root(workspace: WorkspaceLike) -> Path:
    return Path(workspace.absolute_path("."))


def _resolve_artifact_dir(session: CoordinationSessionLike, workspace: WorkspaceLike) -> Path:
    """Use a worker-specific directory when a parallel worker has one."""
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
    """Read the opt-in history policy without making submission depend on policy I/O."""
    del artifact_type
    try:
        policy = load_policy(workspace_root / ".agent")
        return any(
            phase.drain == drain and phase.artifact_history is not None and phase.artifact_history.enabled
            for phase in policy.pipeline.phases.values()
        )
    except Exception:
        return False


def _normalize_artifact_payload(artifact_type: str, parsed_content: dict[str, object], **_: object) -> dict[str, object]:
    """Compatibility normalizer for trusted markdown-parser output and legacy reads."""
    normalizers = {
        "plan": normalize_plan_artifact_content,
        "development_result": normalize_development_result_content,
        "commit_message": normalize_commit_message_content,
        "commit_cleanup": normalize_commit_cleanup_content,
        "issues": normalize_issues_content,
        "fix_result": normalize_fix_result_content,
        "smoke_test_result": normalize_smoke_test_result_content,
        "product_spec": normalize_product_spec_content,
        "planning_analysis_decision": normalize_analysis_decision_content,
        "development_analysis_decision": normalize_analysis_decision_content,
        "review_analysis_decision": normalize_analysis_decision_content,
    }
    normalizer = normalizers.get(artifact_type)
    return normalizer(parsed_content) if normalizer is not None else parsed_content


__all__ = [
    "DEFAULT_ARTIFACT_HANDLER_DEPS",
    "ArtifactHandlerDeps",
    "_resolve_artifact_dir",
    "_resolve_history_enabled",
    "_session_drain",
    "_session_run_id",
    "_workspace_root",
    "execute_ops_with_rollback",
]
