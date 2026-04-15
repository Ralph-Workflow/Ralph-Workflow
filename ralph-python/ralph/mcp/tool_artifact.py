"""MCP artifact submission handlers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from ralph.mcp.artifacts import ArtifactSubmitOptions, submit_artifact
from ralph.mcp.commit_message import (
    COMMIT_MESSAGE_TYPE,
    normalize_commit_message_content,
    write_commit_message_artifact,
)
from ralph.mcp.development_result_artifact import (
    DevelopmentResultValidationError,
    normalize_development_result_content,
)
from ralph.mcp.plan_artifact import (
    PLAN_ARTIFACT_TYPE,
    PLAN_SECTION_NAMES,
    PlanArtifactValidationError,
    SectionMode,
    delete_plan_draft,
    finalize_plan_draft,
    load_plan_draft,
    merge_plan_section,
    new_plan_draft,
    normalize_plan_artifact_content,
    save_plan_draft,
    validate_plan_section,
)
from ralph.mcp.tool_coordination import (
    ARTIFACT_SUBMIT_CAPABILITY,
    InvalidParamsError,
    SessionLike,
    ToolContent,
    ToolResult,
    WorkspaceLike,
    require_capability,
)


def handle_submit_artifact(
    session: SessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
) -> ToolResult:
    require_capability(session, ARTIFACT_SUBMIT_CAPABILITY, "Artifact submission")
    artifact_type, parsed_content = _prepare_artifact_submission(params)

    if artifact_type == COMMIT_MESSAGE_TYPE:
        write_commit_message_artifact(_workspace_root(workspace), parsed_content)

    artifact_dir = _artifact_dir(workspace)
    submit_artifact(
        artifact_dir,
        name=artifact_type,
        artifact_type=artifact_type,
        content=parsed_content,
        options=ArtifactSubmitOptions(overwrite=True),
    )
    if artifact_type == PLAN_ARTIFACT_TYPE:
        # Atomic full-plan submission supersedes any partial draft.
        delete_plan_draft(artifact_dir)
    return ToolResult(
        content=[ToolContent.text_content(f"Artifact submitted: {artifact_type}")],
        is_error=False,
    )


def handle_submit_plan_section(
    session: SessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
) -> ToolResult:
    """Validate a single plan section and merge it into the on-disk draft."""
    require_capability(session, ARTIFACT_SUBMIT_CAPABILITY, "Plan section submission")

    section = _required_string(params, "section")
    if section not in PLAN_SECTION_NAMES:
        raise InvalidParamsError(
            f"Unknown plan section '{section}'. Valid sections: {sorted(PLAN_SECTION_NAMES)}"
        )
    raw_content = _required_string(params, "content")
    payload = _parse_content_any(raw_content)
    mode = _section_mode(params)

    try:
        fragment = validate_plan_section(section, payload, mode=mode)
    except PlanArtifactValidationError as exc:
        raise InvalidParamsError(f"[{section}] {exc}") from exc

    artifact_dir = _artifact_dir(workspace)
    draft = load_plan_draft(artifact_dir) or new_plan_draft()
    current_sections = cast("dict[str, object]", draft.get("sections", {}))
    updated_sections = merge_plan_section(current_sections, section, fragment, mode)
    draft["sections"] = updated_sections
    save_plan_draft(artifact_dir, draft)

    staged = sorted(updated_sections.keys())
    return ToolResult(
        content=[
            ToolContent.text_content(
                f"Plan section staged: {section} (mode={mode}). Staged sections: {staged}"
            )
        ],
        is_error=False,
    )


def handle_finalize_plan(
    session: SessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
) -> ToolResult:
    """Validate the staged draft as a whole plan and write plan.json."""
    require_capability(session, ARTIFACT_SUBMIT_CAPABILITY, "Plan finalization")
    del params  # no params

    artifact_dir = _artifact_dir(workspace)
    draft = load_plan_draft(artifact_dir)
    if draft is None:
        raise InvalidParamsError(
            "No plan draft to finalize. Submit plan sections first or use "
            "ralph_submit_artifact with artifact_type='plan'."
        )

    try:
        normalized = finalize_plan_draft(draft)
    except PlanArtifactValidationError as exc:
        raise InvalidParamsError(str(exc)) from exc

    submit_artifact(
        artifact_dir,
        name=PLAN_ARTIFACT_TYPE,
        artifact_type=PLAN_ARTIFACT_TYPE,
        content=normalized,
        options=ArtifactSubmitOptions(overwrite=True),
    )
    delete_plan_draft(artifact_dir)

    return ToolResult(
        content=[ToolContent.text_content(f"Artifact submitted: {PLAN_ARTIFACT_TYPE}")],
        is_error=False,
    )


def handle_get_plan_draft(
    session: SessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
) -> ToolResult:
    """Return the current plan draft so an agent can resume after a restart."""
    require_capability(session, ARTIFACT_SUBMIT_CAPABILITY, "Plan draft read")
    del params

    artifact_dir = _artifact_dir(workspace)
    draft = load_plan_draft(artifact_dir)
    if draft is None:
        response: dict[str, object] = {"staged_sections": []}
    else:
        sections = cast("dict[str, object]", draft.get("sections", {}))
        response = {
            "staged_sections": sorted(sections.keys()),
            "started_at": draft.get("started_at"),
            "updated_at": draft.get("updated_at"),
            "draft": sections,
        }

    return ToolResult(
        content=[ToolContent.text_content(json.dumps(response))],
        is_error=False,
    )


def handle_discard_plan_draft(
    session: SessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
) -> ToolResult:
    """Delete the on-disk plan draft so the agent can start over."""
    require_capability(session, ARTIFACT_SUBMIT_CAPABILITY, "Plan draft discard")
    del params

    existed = delete_plan_draft(_artifact_dir(workspace))
    text = "Plan draft discarded." if existed else "No plan draft to discard."
    return ToolResult(
        content=[ToolContent.text_content(text)],
        is_error=False,
    )


def _required_string(params: dict[str, object], name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str):
        raise InvalidParamsError(f"Missing '{name}' parameter")
    return value


def _section_mode(params: dict[str, object]) -> SectionMode:
    raw = params.get("mode", "replace")
    if raw not in ("replace", "append"):
        raise InvalidParamsError("'mode' must be 'replace' or 'append'")
    return raw


def _parse_content(raw_content: str) -> dict[str, object]:
    parsed = _parse_content_any(raw_content)
    if not isinstance(parsed, dict):
        raise InvalidParamsError("Artifact content must decode to a JSON object")
    return cast("dict[str, object]", parsed)


def _parse_content_any(raw_content: str) -> object:
    try:
        return cast("object", json.loads(raw_content))
    except json.JSONDecodeError as exc:
        raise InvalidParamsError(f"Content must be valid JSON: {exc}") from exc


def _workspace_root(workspace: WorkspaceLike) -> Path:
    return Path(workspace.absolute_path("."))


def _artifact_dir(workspace: WorkspaceLike) -> Path:
    return Path(workspace.absolute_path(".agent/artifacts"))


def _prepare_artifact_submission(params: dict[str, object]) -> tuple[str, dict[str, object]]:
    artifact_type = _required_string(params, "artifact_type")
    raw_content = _required_string(params, "content")
    parsed_content = _parse_content(raw_content)

    if artifact_type in {"commit", "skip"}:
        artifact_type = COMMIT_MESSAGE_TYPE

    if artifact_type == COMMIT_MESSAGE_TYPE:
        if "message" in parsed_content:
            raise InvalidParamsError(
                "commit_message artifacts must use the structured commit_message schema; "
                "legacy 'message' payloads are no longer accepted"
            )
        try:
            parsed_content = normalize_commit_message_content(parsed_content)
        except ValueError as exc:
            raise InvalidParamsError(str(exc)) from exc
    elif artifact_type == PLAN_ARTIFACT_TYPE:
        try:
            parsed_content = normalize_plan_artifact_content(parsed_content)
        except PlanArtifactValidationError as exc:
            raise InvalidParamsError(str(exc)) from exc
    elif artifact_type == "development_result":
        try:
            parsed_content = normalize_development_result_content(parsed_content)
        except DevelopmentResultValidationError as exc:
            raise InvalidParamsError(str(exc)) from exc

    return artifact_type, parsed_content


__all__ = [
    "_prepare_artifact_submission",
    "handle_discard_plan_draft",
    "handle_finalize_plan",
    "handle_get_plan_draft",
    "handle_submit_artifact",
    "handle_submit_plan_section",
]
