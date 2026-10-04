"""Work-units policy check helpers used during markdown artifact validation.

These helpers are extracted from ``md_artifact`` so that ``md_artifact`` can
stay under the workspace file-size audit cap. They depend only on already
imported symbols in ``md_artifact`` and a small handful of Ralph policy /
workspace modules; importing them from here keeps their public behaviour
identical to the inlined version.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ralph.mcp.artifacts.markdown import Diagnostic, parse_markdown_document
from ralph.mcp.artifacts.plan.plan_schema import ParallelPlanItem
from ralph.mcp.tools.artifact import _workspace_root
from ralph.pipeline.work_units import (
    WorkUnitsValidationError,
    parse_work_units_from_artifact,
)
from ralph.policy import loader as policy_loader
from ralph.policy.validation import validate_work_units_against_policy

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.tools.coordination import WorkspaceLike
    from ralph.policy.models import PipelinePolicy


def work_units_policy_check(
    workspace: WorkspaceLike,
    artifact_type: str,
    parsed_content: dict[str, object],
    content: str,
) -> list[Diagnostic]:
    """Validate plan work_units against the workspace's effective pipeline policy.

    Returns an empty list when the artifact is not a plan, has no parallel
    units, or has already failed structural validation. Both Work Units and
    Parallel Plan use the effective policy; a policy-load error blocks
    verification and submission instead of accepting unchecked ownership.

    Single diagnostic rule (``WUPOL001``) covers every per-unit and cap
    violation: the validator's error message names the violated limit and
    its value, and the diagnostic anchors on the line of the
    ``## Work Units`` section heading.
    """
    diagnostics: list[Diagnostic] = []
    if artifact_type != "plan":
        return diagnostics
    section_name = "Work Units"
    raw = parsed_content.get("work_units")
    if not raw:
        raw_parallel = parsed_content.get("parallel_plan")
        if isinstance(raw_parallel, list) and raw_parallel:
            section_name = "Parallel Plan"
            try:
                parallel_items = TypeAdapter(list[ParallelPlanItem]).validate_python(raw_parallel)
            except ValueError:
                return diagnostics
            raw = [
                {
                    "unit_id": item.id,
                    "description": item.description,
                    "allowed_directories": item.edit_area.directories + item.edit_area.paths,
                    "dependencies": item.depends_on,
                }
                for item in parallel_items
            ]
    if not raw:
        return diagnostics

    try:
        parsed = parse_work_units_from_artifact({"work_units": raw})
    except (WorkUnitsValidationError, ValueError) as exc:
        return [
            Diagnostic(
                _section_line(content, section_name) or 1, section_name, "WUPOL001", str(exc)
            )
        ]
    if parsed is None:
        return diagnostics

    workspace_root = _workspace_root(workspace)
    section_line = _section_line(content, section_name) or 1

    try:
        pipeline = load_policy_pipeline(workspace_root)
        validate_work_units_against_policy(
            parsed, pipeline, phase="development", planning_intent=True
        )
    except Exception as exc:
        diagnostics.append(
            Diagnostic(
                section_line,
                section_name,
                "WUPOL001",
                str(exc),
            )
        )
    return diagnostics


def _section_line(content: str, section_name: str) -> int | None:
    """Return the 1-based line of the first ``## {section_name}`` heading, or None."""
    try:
        document, _ = parse_markdown_document(content, allow_nested_headings=False)
    except Exception:
        return None
    section = document.section(section_name)
    if section is None:
        return None
    return section.line


def load_policy_pipeline(workspace_root: Path) -> PipelinePolicy:
    """Load the workspace's effective pipeline policy, propagating load errors.

    Public hook so tests can replace it via ``monkeypatch.setattr`` without
    importing the underscored helper module.
    """
    bundle = policy_loader.load_policy(workspace_root / ".agent")
    return bundle.pipeline
