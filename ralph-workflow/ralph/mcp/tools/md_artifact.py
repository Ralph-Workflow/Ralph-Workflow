"""MCP handlers for validated markdown artifact documents."""

from __future__ import annotations

from dataclasses import asdict
from importlib import import_module
from typing import cast

from ralph.mcp.artifacts.canonical_submit import submit_artifact_canonical
from ralph.mcp.artifacts.markdown import Diagnostic, parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.markdown.specs.plan import edit_plan_step_markdown
from ralph.mcp.tools.artifact import (
    DEFAULT_ARTIFACT_HANDLER_DEPS,
    ArtifactHandlerDeps,
    _resolve_artifact_dir,
    _resolve_history_enabled,
    _session_drain,
    _session_run_id,
    _workspace_root,
)
from ralph.mcp.tools.coordination import (
    ARTIFACT_SUBMIT_CAPABILITY,
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    ToolResult,
    WorkspaceLike,
    require_capability,
)

_PLAN_READ_CAPABILITY = "artifact.plan_read"


def handle_verify_md_artifact(
    session: CoordinationSessionLike,
    _workspace: WorkspaceLike,
    params: dict[str, object],
) -> ToolResult:
    """Check a markdown artifact without writing it."""
    require_capability(session, _PLAN_READ_CAPABILITY, "Markdown artifact verification")
    artifact_type, content = _params(params)
    _, diagnostics = parse_and_validate(content, get_spec(artifact_type))
    return _validation_result(artifact_type, diagnostics)


def handle_submit_md_artifact(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Validate and canonically persist a markdown artifact atomically."""
    require_capability(session, ARTIFACT_SUBMIT_CAPABILITY, "Markdown artifact submission")
    artifact_type, content = _params(params)
    parsed_content, diagnostics = parse_and_validate(content, get_spec(artifact_type))
    result = _validation_result(artifact_type, diagnostics)
    if result.is_error:
        return result

    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS
    workspace_root = _workspace_root(workspace)
    effective_deps = ArtifactHandlerDeps(
        backend=resolved_deps.backend,
        now_iso=resolved_deps.now_iso,
        history_enabled=_resolve_history_enabled(
            artifact_type, workspace_root, _session_drain(session)
        ),
        receipt_secret=session.broker_secret,
    )
    submit_artifact_canonical(
        workspace_root=workspace_root,
        artifact_type=artifact_type,
        parsed_content=parsed_content,
        markdown=content,
        deps=effective_deps,
        run_id=_session_run_id(session),
        artifact_dir=_resolve_artifact_dir(session, workspace),
    )
    return result


def handle_edit_md_plan_step(
    session: CoordinationSessionLike,
    _workspace: WorkspaceLike,
    params: dict[str, object],
) -> ToolResult:
    """Edit one plan step by stable ID without changing unrelated markdown."""
    require_capability(session, ARTIFACT_SUBMIT_CAPABILITY, "Markdown plan-step editing")
    content = params.get("content")
    action = params.get("action")
    step_id = params.get("step_id")
    replacement = params.get("replacement")
    index = params.get("index")
    if not isinstance(content, str) or not isinstance(action, str) or not isinstance(step_id, str):
        raise InvalidParamsError("content, action, and step_id are required")
    if replacement is not None and not isinstance(replacement, dict):
        raise InvalidParamsError("replacement must be an object")
    if index is not None and not isinstance(index, int):
        raise InvalidParamsError("index must be an integer")
    edited = edit_plan_step_markdown(
        content,
        action,
        step_id,
        cast("dict[str, object] | None", replacement),
        index,
    )
    return ToolResult(content=[ToolContent.json_content({"content": edited})], is_error=False)


def _params(params: dict[str, object]) -> tuple[str, str]:
    import_module("ralph.mcp.artifacts.markdown.specs")
    artifact_type = params.get("artifact_type")
    content = params.get("content")
    if not isinstance(artifact_type, str) or not artifact_type:
        raise InvalidParamsError("Missing 'artifact_type' parameter")
    if not isinstance(content, str):
        raise InvalidParamsError("Missing 'content' markdown parameter")
    try:
        get_spec(artifact_type)
    except ValueError as exc:
        raise InvalidParamsError(str(exc)) from exc
    return artifact_type, content


def _validation_result(artifact_type: str, diagnostics: list[Diagnostic]) -> ToolResult:
    return ToolResult(
        content=[
            ToolContent.json_content(
                {
                    "artifact_type": artifact_type,
                    "valid": not any(item.severity == "error" for item in diagnostics),
                    "diagnostics": [cast("dict[str, object]", asdict(item)) for item in diagnostics],
                }
            )
        ],
        is_error=any(item.severity == "error" for item in diagnostics),
    )


__all__ = ["handle_edit_md_plan_step", "handle_submit_md_artifact", "handle_verify_md_artifact"]
