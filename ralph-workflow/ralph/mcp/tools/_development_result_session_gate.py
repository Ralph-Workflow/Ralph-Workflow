"""Session-bound development-result submission validation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ralph.mcp.artifacts.markdown import Diagnostic
from ralph.mcp.artifacts.markdown.specs.plan import analyze_plan_document
from ralph.mcp.artifacts.plan._section_registry import PLAN_ARTIFACT_PATH
from ralph.mcp.tools.artifact import (
    DEFAULT_ARTIFACT_HANDLER_DEPS,
    ArtifactHandlerDeps,
    _workspace_root,
)
from ralph.pipeline.work_units import canonical_plan_references

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.tools.coordination import CoordinationSessionLike, WorkspaceLike


@runtime_checkable
class _WorkerSession(Protocol):
    worker_namespace: Path | None


def development_result_session_diagnostics(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    content: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> list[Diagnostic]:
    """Enforce exact proof coverage for completed development results."""
    if content.get("status") != "completed":
        return []
    submitted_refs: set[str] = set()
    raw_proofs = content.get("plan_items_proven")
    if isinstance(raw_proofs, list):
        for proof in raw_proofs:
            if isinstance(proof, dict):
                plan_item = proof.get("plan_item")
                if isinstance(plan_item, str):
                    submitted_refs.add(plan_item)
    required_refs = _required_plan_refs(session, workspace, deps, submitted_refs)
    if not required_refs:
        return []
    if required_refs == submitted_refs and isinstance(raw_proofs, list) and len(raw_proofs) == len(submitted_refs):
        return []
    return [
        Diagnostic(
            1,
            "Plan Items Proven",
            "DEV015",
            "completed development results require proof for every plan item; "
            f"missing={sorted(required_refs - submitted_refs)}, "
            f"unexpected={sorted(submitted_refs - required_refs)}",
        )
    ]


def _required_plan_refs(
    session: CoordinationSessionLike, workspace: WorkspaceLike, deps: ArtifactHandlerDeps | None,
    submitted_refs: set[str],
) -> set[str]:
    backend = (deps or DEFAULT_ARTIFACT_HANDLER_DEPS).backend
    plan_path = _workspace_root(workspace) / PLAN_ARTIFACT_PATH
    if not backend.exists(plan_path):
        return set()
    plan_content, _, _ = analyze_plan_document(
        backend.read_text(plan_path, encoding="utf-8")
    )
    step_refs, unit_refs, owned_steps = canonical_plan_references(plan_content)
    worker_namespace = session.worker_namespace if isinstance(session, _WorkerSession) else None
    if worker_namespace is not None and worker_namespace.name in unit_refs:
        return {worker_namespace.name}
    if step_refs and submitted_refs and submitted_refs <= step_refs:
        return set(step_refs)
    return set(unit_refs | (step_refs - owned_steps)) or {"plan"}


__all__ = ["development_result_session_diagnostics"]
