"""Session-bound development-result submission validation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ralph.mcp.artifacts.markdown import Diagnostic
from ralph.mcp.artifacts.markdown.specs.plan import analyze_plan_document
from ralph.mcp.artifacts.plan._section_registry import PLAN_ARTIFACT_PATH
from ralph.mcp.server._session_wrapup import session_before_warning
from ralph.mcp.tools.artifact import DEFAULT_ARTIFACT_HANDLER_DEPS, _workspace_root
from ralph.pipeline.work_units import parse_work_units_from_artifact

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.tools.coordination import CoordinationSessionLike, WorkspaceLike


@runtime_checkable
class _WorkerSession(Protocol):
    worker_namespace: Path | None


def pre_warning_development_result_diagnostics(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    content: dict[str, object],
) -> list[Diagnostic]:
    """Reject incomplete or unproven development results before the warning."""
    if not session_before_warning():
        return []
    if content.get("status") != "completed":
        return [
            Diagnostic(
                1,
                "Frontmatter",
                "DEV014",
                "development_result status must be 'completed' before the session's 50-minute warning",
            )
        ]
    required_refs = _required_plan_refs(session, workspace)
    submitted_refs: set[str] = set()
    raw_proofs = content.get("plan_items_proven")
    if isinstance(raw_proofs, list):
        for proof in raw_proofs:
            if isinstance(proof, dict):
                plan_item = proof.get("plan_item")
                if isinstance(plan_item, str):
                    submitted_refs.add(plan_item)
    if required_refs == submitted_refs:
        return []
    return [
        Diagnostic(
            1,
            "Plan Items Proven",
            "DEV015",
            "pre-warning completion requires proof for every plan item; "
            f"missing={sorted(required_refs - submitted_refs)}, "
            f"unexpected={sorted(submitted_refs - required_refs)}",
        )
    ]


def _required_plan_refs(session: CoordinationSessionLike, workspace: WorkspaceLike) -> set[str]:
    backend = DEFAULT_ARTIFACT_HANDLER_DEPS.backend
    plan_path = _workspace_root(workspace) / PLAN_ARTIFACT_PATH
    if not backend.exists(plan_path):
        return set()
    plan_content, diagnostics, _ = analyze_plan_document(
        backend.read_text(plan_path, encoding="utf-8")
    )
    if any(item.severity == "error" for item in diagnostics):
        return set()
    steps = plan_content.get("steps")
    step_refs: set[str] = set()
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            step_id = step.get("id") or step.get("step_id")
            if isinstance(step_id, str):
                step_refs.add(step_id)
            elif isinstance(step.get("number"), int):
                step_refs.add(f"S-{step['number']}")
    parsed_units = parse_work_units_from_artifact(plan_content)
    if parsed_units is None or not parsed_units.work_units:
        return step_refs
    worker_namespace = session.worker_namespace if isinstance(session, _WorkerSession) else None
    if worker_namespace is not None:
        return {worker_namespace.name}
    unit_refs = {unit.unit_id for unit in parsed_units.work_units}
    owned_steps = {step_id for unit in parsed_units.work_units for step_id in unit.step_ids}
    return unit_refs | (step_refs - owned_steps)


__all__ = ["pre_warning_development_result_diagnostics"]
