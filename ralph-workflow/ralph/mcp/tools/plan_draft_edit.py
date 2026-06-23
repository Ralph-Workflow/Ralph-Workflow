"""Precise MCP handlers for plan draft step edits."""

from __future__ import annotations

from typing import cast

from ralph.mcp.artifacts.plan import (
    PlanArtifactValidationError,
    insert_plan_step_with_echo,
    move_plan_step_with_echo,
    remove_plan_step_with_echo,
    replace_plan_step_with_echo,
)
from ralph.mcp.tools.coordination import (
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    ToolResult,
    WorkspaceLike,
    require_capability,
)
from ralph.mcp.tools.json_repair import repair_json_containers

from .artifact import (
    DEFAULT_ARTIFACT_HANDLER_DEPS,
    PLAN_DRAFT_WRITE_CAPABILITY,
    ArtifactHandlerDeps,
    _format_plan_step_edit_error,
    _load_or_create_plan_draft,
    _resolve_artifact_dir,
    _save_updated_plan_draft,
    _workspace_root,
)


def handle_insert_plan_step(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Insert a single plan step and reindex the draft deterministically.

    Auto-reindexes the remaining steps, rewrites every ``depends_on``
    array in the surviving steps to use the new step numbers, and
    rewrites every ``AC.satisfied_by_steps`` reference in the design
    sub-section to use the new step numbers; the provided
    ``step.number`` is ignored.

    Returns a JSON echo payload with the new step number, the reindex
    map, the list of step numbers whose ``depends_on`` was rewritten,
    the list of AC ids whose ``satisfied_by_steps`` was rewritten, the
    list of AC ids whose ``satisfied_by_steps`` entries were dropped,
    and the new total step count.
    """
    require_capability(session, PLAN_DRAFT_WRITE_CAPABILITY, "Plan step insertion")
    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS
    workspace_root = _workspace_root(workspace)
    try:
        index = _required_int(params, "index")
    except InvalidParamsError as exc:
        raise InvalidParamsError(
            _format_plan_step_edit_error(
                detail=str(exc),
                workspace_root=workspace_root,
                backend=resolved_deps.backend,
                tool_name="ralph_insert_plan_step",
            )
        ) from exc
    step_payload = _step_payload_from_params(params)

    artifact_dir = _resolve_artifact_dir(session, workspace)
    draft = _load_or_create_plan_draft(artifact_dir, deps=resolved_deps)
    current_sections = cast("dict[str, object]", draft.get("sections", {}))
    try:
        updated_sections, echo = insert_plan_step_with_echo(
            current_sections,
            index=index,
            step_payload=step_payload,
        )
    except PlanArtifactValidationError as exc:
        raise InvalidParamsError(
            _format_plan_step_edit_error(
                detail=str(exc),
                workspace_root=workspace_root,
                backend=resolved_deps.backend,
                tool_name="ralph_insert_plan_step",
            )
        ) from exc
    draft["sections"] = updated_sections
    _save_updated_plan_draft(artifact_dir, draft, deps=resolved_deps)
    return ToolResult(
        content=[ToolContent.json_content(echo)],
        is_error=False,
    )


def handle_replace_plan_step(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Replace a single plan step and reindex the draft deterministically.

    Auto-reindexes the remaining steps, rewrites every ``depends_on``
    array in the surviving steps to use the new step numbers, and
    rewrites every ``AC.satisfied_by_steps`` reference in the design
    sub-section to use the new step numbers; the provided
    ``step.number`` is ignored.

    Returns a JSON echo payload with the (unchanged) step number, the
    reindex map (typically a no-op), the list of step numbers whose
    ``depends_on`` was rewritten, the list of AC ids whose
    ``satisfied_by_steps`` was rewritten, the list of AC ids whose
    ``satisfied_by_steps`` entries were dropped, and the new total step
    count.
    """
    require_capability(session, PLAN_DRAFT_WRITE_CAPABILITY, "Plan step replacement")
    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS
    workspace_root = _workspace_root(workspace)
    try:
        step_number = _required_int(params, "step_number")
    except InvalidParamsError as exc:
        raise InvalidParamsError(
            _format_plan_step_edit_error(
                detail=str(exc),
                workspace_root=workspace_root,
                backend=resolved_deps.backend,
                tool_name="ralph_replace_plan_step",
            )
        ) from exc
    step_payload = _step_payload_from_params(params)

    artifact_dir = _resolve_artifact_dir(session, workspace)
    draft = _load_or_create_plan_draft(artifact_dir, deps=resolved_deps)
    current_sections = cast("dict[str, object]", draft.get("sections", {}))
    try:
        updated_sections, echo = replace_plan_step_with_echo(
            current_sections,
            step_number=step_number,
            step_payload=step_payload,
        )
    except PlanArtifactValidationError as exc:
        raise InvalidParamsError(
            _format_plan_step_edit_error(
                detail=str(exc),
                workspace_root=workspace_root,
                backend=resolved_deps.backend,
                tool_name="ralph_replace_plan_step",
            )
        ) from exc
    draft["sections"] = updated_sections
    _save_updated_plan_draft(artifact_dir, draft, deps=resolved_deps)
    return ToolResult(
        content=[ToolContent.json_content(echo)],
        is_error=False,
    )


def handle_remove_plan_step(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Remove a single plan step and reindex the draft deterministically.

    Auto-reindexes the remaining steps, rewrites every ``depends_on``
    array in the surviving steps to use the new step numbers, and
    rewrites every ``AC.satisfied_by_steps`` reference in the design
    sub-section to use the new step numbers; the provided
    ``step.number`` is ignored.

    Fails fast with ``PlanArtifactValidationError`` if any other step
    depends on the removed step. Silently drops AC entries whose
    ``satisfied_by_steps`` reference the removed step. Returns a JSON
    echo payload with the removed step number, the reindex map, the
    list of step numbers whose ``depends_on`` was rewritten, the list
    of AC ids whose ``satisfied_by_steps`` was rewritten, the list of
    AC ids whose ``satisfied_by_steps`` entries were dropped, and the
    new total step count.
    """
    require_capability(session, PLAN_DRAFT_WRITE_CAPABILITY, "Plan step removal")
    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS
    workspace_root = _workspace_root(workspace)
    try:
        step_number = _required_int(params, "step_number")
    except InvalidParamsError as exc:
        raise InvalidParamsError(
            _format_plan_step_edit_error(
                detail=str(exc),
                workspace_root=workspace_root,
                backend=resolved_deps.backend,
                tool_name="ralph_remove_plan_step",
            )
        ) from exc

    artifact_dir = _resolve_artifact_dir(session, workspace)
    draft = _load_or_create_plan_draft(artifact_dir, deps=resolved_deps)
    current_sections = cast("dict[str, object]", draft.get("sections", {}))
    try:
        updated_sections, echo = remove_plan_step_with_echo(
            current_sections, step_number=step_number
        )
    except PlanArtifactValidationError as exc:
        raise InvalidParamsError(
            _format_plan_step_edit_error(
                detail=str(exc),
                workspace_root=workspace_root,
                backend=resolved_deps.backend,
                tool_name="ralph_remove_plan_step",
            )
        ) from exc
    draft["sections"] = updated_sections
    _save_updated_plan_draft(artifact_dir, draft, deps=resolved_deps)
    return ToolResult(
        content=[ToolContent.json_content(echo)],
        is_error=False,
    )


def handle_move_plan_step(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Move a single plan step to a new index and reindex the draft deterministically.

    Auto-reindexes the surviving steps, rewrites every ``depends_on``
    array in the surviving steps to use the new step numbers, and
    rewrites every ``AC.satisfied_by_steps`` reference in the design
    sub-section to use the new step numbers; the provided
    ``step.number`` is ignored.

    Returns a JSON echo payload with the source and target step numbers
    (typically identical since move preserves step numbers), the reindex
    map (typically a no-op), the list of step numbers whose
    ``depends_on`` was rewritten, the list of AC ids whose
    ``satisfied_by_steps`` was rewritten, the list of AC ids whose
    ``satisfied_by_steps`` entries were dropped, and the new total step
    count.
    """
    require_capability(session, PLAN_DRAFT_WRITE_CAPABILITY, "Plan step move")
    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS
    workspace_root = _workspace_root(workspace)
    try:
        from_step_number = _required_int(params, "from_step_number")
        to_index = _required_int(params, "to_index")
    except InvalidParamsError as exc:
        raise InvalidParamsError(
            _format_plan_step_edit_error(
                detail=str(exc),
                workspace_root=workspace_root,
                backend=resolved_deps.backend,
                tool_name="ralph_move_plan_step",
            )
        ) from exc

    artifact_dir = _resolve_artifact_dir(session, workspace)
    draft = _load_or_create_plan_draft(artifact_dir, deps=resolved_deps)
    current_sections = cast("dict[str, object]", draft.get("sections", {}))
    try:
        updated_sections, echo = move_plan_step_with_echo(
            current_sections,
            from_step_number=from_step_number,
            to_index=to_index,
        )
    except PlanArtifactValidationError as exc:
        raise InvalidParamsError(
            _format_plan_step_edit_error(
                detail=str(exc),
                workspace_root=workspace_root,
                backend=resolved_deps.backend,
                tool_name="ralph_move_plan_step",
            )
        ) from exc
    draft["sections"] = updated_sections
    _save_updated_plan_draft(artifact_dir, draft, deps=resolved_deps)
    return ToolResult(
        content=[ToolContent.json_content(echo)],
        is_error=False,
    )


def handle_patch_step(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Partial-update a single plan step (shallow-merge).

    Pass ``step_number`` and a step dict with ANY SUBSET of step fields;
    the missing fields are preserved from the existing step. The provided
    ``step.number`` is ignored (``replace_plan_step`` forces the number
    to ``step_number``). The step-mutation auto-reindex of
    ``depends_on`` and ``AC.satisfied_by_steps`` runs as for
    ``ralph_replace_plan_step``. Returns the same echo payload shape
    as ``handle_replace_plan_step``.
    """
    require_capability(session, PLAN_DRAFT_WRITE_CAPABILITY, "Plan step patch")
    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS
    workspace_root = _workspace_root(workspace)
    try:
        step_number = _required_int(params, "step_number")
    except InvalidParamsError as exc:
        raise InvalidParamsError(
            _format_plan_step_edit_error(
                detail=str(exc),
                workspace_root=workspace_root,
                backend=resolved_deps.backend,
                tool_name="ralph_patch_step",
            )
        ) from exc
    step_payload = _step_payload_from_params(params)
    if not isinstance(step_payload, dict):
        raise InvalidParamsError(
            _format_plan_step_edit_error(
                detail="Missing 'step' object",
                workspace_root=_workspace_root(workspace),
                backend=(deps or DEFAULT_ARTIFACT_HANDLER_DEPS).backend,
                tool_name="ralph_patch_step",
            )
        )
    artifact_dir = _resolve_artifact_dir(session, workspace)
    draft = _load_or_create_plan_draft(artifact_dir, deps=resolved_deps)
    current_sections = cast("dict[str, object]", draft.get("sections", {}))

    steps_obj = current_sections.get("steps")
    existing_step: dict[str, object] | None = None
    if isinstance(steps_obj, list):
        for step in steps_obj:
            if isinstance(step, dict) and cast("int", step.get("number")) == step_number:
                existing_step = step
                break
    if existing_step is None:
        raise InvalidParamsError(
            _format_plan_step_edit_error(
                detail=f"Step {step_number} does not exist",
                workspace_root=workspace_root,
                backend=resolved_deps.backend,
                tool_name="ralph_patch_step",
            )
        )

    merged = dict(existing_step)
    for key, value in cast("dict[str, object]", step_payload).items():
        if key == "number":
            continue
        merged[key] = value

    try:
        updated_sections, echo = replace_plan_step_with_echo(
            current_sections,
            step_number=step_number,
            step_payload=merged,
        )
    except PlanArtifactValidationError as exc:
        raise InvalidParamsError(
            _format_plan_step_edit_error(
                detail=str(exc),
                workspace_root=workspace_root,
                backend=resolved_deps.backend,
                tool_name="ralph_patch_step",
            )
        ) from exc
    draft["sections"] = updated_sections
    _save_updated_plan_draft(artifact_dir, draft, deps=resolved_deps)
    return ToolResult(
        content=[ToolContent.json_content(echo)],
        is_error=False,
    )


def _required_int(params: dict[str, object], name: str) -> int:
    value = params.get(name)
    if isinstance(value, bool):
        raise InvalidParamsError(f"Missing '{name}' parameter")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and stripped.lstrip("-").isdigit():
            return int(stripped)
    raise InvalidParamsError(f"Missing '{name}' parameter")


_STEP_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {
        "number",
        "title",
        "content",
        "step_type",
        "priority",
        "targets",
        "location",
        "rationale",
        "depends_on",
        "satisfies",
        "expected_evidence",
        "verify_command",
    }
)


def _step_payload_from_params(params: dict[str, object]) -> object:
    """Return a repaired step object from either ``step`` or flat step args."""
    raw_step = params.get("step")
    if raw_step is not None:
        return repair_json_containers(raw_step)
    flat_step = {
        key: value
        for key, value in params.items()
        if key in _STEP_PAYLOAD_KEYS
    }
    if not flat_step:
        return None
    return repair_json_containers(flat_step)


__all__ = [
    "handle_insert_plan_step",
    "handle_move_plan_step",
    "handle_patch_step",
    "handle_remove_plan_step",
    "handle_replace_plan_step",
]
