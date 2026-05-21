"""Precise MCP handlers for plan draft step edits."""

from __future__ import annotations

from typing import cast

from ralph.mcp.artifacts.plan import (
    PlanArtifactValidationError,
    insert_plan_step,
    remove_plan_step,
    replace_plan_step,
)
from ralph.mcp.tools.coordination import (
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    ToolResult,
    WorkspaceLike,
    require_capability,
)

from .artifact import (
    DEFAULT_ARTIFACT_HANDLER_DEPS,
    PLAN_DRAFT_WRITE_CAPABILITY,
    ArtifactHandlerDeps,
    _load_or_create_plan_draft,
    _resolve_artifact_dir,
    _save_updated_plan_draft,
)


def handle_insert_plan_step(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Insert a single plan step and reindex the draft deterministically."""
    require_capability(session, PLAN_DRAFT_WRITE_CAPABILITY, "Plan step insertion")
    index = _required_int(params, "index")
    step_payload = params.get("step")
    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS

    artifact_dir = _resolve_artifact_dir(session, workspace)
    draft = _load_or_create_plan_draft(artifact_dir, deps=resolved_deps)
    current_sections = cast("dict[str, object]", draft.get("sections", {}))
    try:
        updated_sections = insert_plan_step(
            current_sections,
            index=index,
            step_payload=step_payload,
        )
    except PlanArtifactValidationError as exc:
        raise InvalidParamsError(str(exc)) from exc
    draft["sections"] = updated_sections
    _save_updated_plan_draft(artifact_dir, draft, deps=resolved_deps)
    return ToolResult(
        content=[ToolContent.text_content(f"Plan step inserted at index {index}.")],
        is_error=False,
    )


def handle_replace_plan_step(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Replace a single plan step and reindex the draft deterministically."""
    require_capability(session, PLAN_DRAFT_WRITE_CAPABILITY, "Plan step replacement")
    step_number = _required_int(params, "step_number")
    step_payload = params.get("step")
    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS

    artifact_dir = _resolve_artifact_dir(session, workspace)
    draft = _load_or_create_plan_draft(artifact_dir, deps=resolved_deps)
    current_sections = cast("dict[str, object]", draft.get("sections", {}))
    try:
        updated_sections = replace_plan_step(
            current_sections,
            step_number=step_number,
            step_payload=step_payload,
        )
    except PlanArtifactValidationError as exc:
        raise InvalidParamsError(str(exc)) from exc
    draft["sections"] = updated_sections
    _save_updated_plan_draft(artifact_dir, draft, deps=resolved_deps)
    return ToolResult(
        content=[ToolContent.text_content(f"Plan step {step_number} replaced.")],
        is_error=False,
    )


def handle_remove_plan_step(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Remove a single plan step and reindex the draft deterministically."""
    require_capability(session, PLAN_DRAFT_WRITE_CAPABILITY, "Plan step removal")
    step_number = _required_int(params, "step_number")
    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS

    artifact_dir = _resolve_artifact_dir(session, workspace)
    draft = _load_or_create_plan_draft(artifact_dir, deps=resolved_deps)
    current_sections = cast("dict[str, object]", draft.get("sections", {}))
    try:
        updated_sections = remove_plan_step(current_sections, step_number=step_number)
    except PlanArtifactValidationError as exc:
        raise InvalidParamsError(str(exc)) from exc
    draft["sections"] = updated_sections
    _save_updated_plan_draft(artifact_dir, draft, deps=resolved_deps)
    return ToolResult(
        content=[ToolContent.text_content(f"Plan step {step_number} removed.")],
        is_error=False,
    )


def _required_int(params: dict[str, object], name: str) -> int:
    value = params.get(name)
    if not isinstance(value, int):
        raise InvalidParamsError(f"Missing '{name}' parameter")
    return value


__all__ = [
    "handle_insert_plan_step",
    "handle_remove_plan_step",
    "handle_replace_plan_step",
]
