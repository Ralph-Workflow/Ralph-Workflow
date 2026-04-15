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
    PlanArtifactValidationError,
    normalize_plan_artifact_content,
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

    artifact_dir = Path(workspace.absolute_path(".agent/artifacts"))
    submit_artifact(
        artifact_dir,
        name=artifact_type,
        artifact_type=artifact_type,
        content=parsed_content,
        options=ArtifactSubmitOptions(overwrite=True),
    )
    return ToolResult(
        content=[ToolContent.text_content(f"Artifact submitted: {artifact_type}")],
        is_error=False,
    )


def _required_string(params: dict[str, object], name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str):
        raise InvalidParamsError(f"Missing '{name}' parameter")
    return value


def _parse_content(raw_content: str) -> dict[str, object]:
    try:
        parsed = cast("object", json.loads(raw_content))
    except json.JSONDecodeError as exc:
        raise InvalidParamsError(f"Artifact content must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise InvalidParamsError("Artifact content must decode to a JSON object")
    return cast("dict[str, object]", parsed)


def _workspace_root(workspace: WorkspaceLike) -> Path:
    return Path(workspace.absolute_path("."))


def _prepare_artifact_submission(params: dict[str, object]) -> tuple[str, dict[str, object]]:
    artifact_type = _required_string(params, "artifact_type")
    raw_content = _required_string(params, "content")
    parsed_content = _parse_content(raw_content)

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


__all__ = ["_prepare_artifact_submission", "handle_submit_artifact"]
