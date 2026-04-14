"""MCP artifact submission handlers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from ralph.mcp.artifacts import ArtifactSubmitOptions, submit_artifact
from ralph.mcp.commit_message import COMMIT_MESSAGE_TYPE, write_commit_message_artifact
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
    artifact_type = _required_string(params, "artifact_type")
    raw_content = _required_string(params, "content")
    parsed_content = _parse_content(raw_content)

    if artifact_type == COMMIT_MESSAGE_TYPE:
        message = parsed_content.get("message")
        if not isinstance(message, str) or not message.strip():
            raise InvalidParamsError("commit_message artifacts require non-empty 'message'")
        write_commit_message_artifact(_workspace_root(workspace), message.strip())

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


__all__ = ["handle_submit_artifact"]
