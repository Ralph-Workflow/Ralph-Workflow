"""MCP artifact submission handlers."""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from ralph.mcp.artifacts import (
    DEFAULT_ARTIFACT_PERSISTENCE,
    ArtifactPersistence,
    ArtifactSubmitOptions,
    delete_artifact,
    submit_artifact,
)
from ralph.mcp.commit_message import (
    COMMIT_MESSAGE_TYPE,
    delete_commit_message_artifacts,
    normalize_commit_message_content,
    write_commit_message_artifact,
)
from ralph.mcp.development_result_artifact import (
    DEVELOPMENT_RESULT_ARTIFACT_TYPE,
    DevelopmentResultValidationError,
    normalize_development_result_content,
)
from ralph.mcp.file_backend import DEFAULT_FILE_BACKEND, FileBackend
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

if TYPE_CHECKING:
    from collections.abc import Callable


def _noop_now_iso() -> str:
    return DEFAULT_ARTIFACT_PERSISTENCE.now_iso()


@dataclass(frozen=True)
class ArtifactHandlerDeps:
    backend: FileBackend = DEFAULT_FILE_BACKEND
    now_iso: Callable[[], str] = _noop_now_iso

    @property
    def artifact_persistence(self) -> ArtifactPersistence:
        return ArtifactPersistence(backend=self.backend, now_iso=self.now_iso)


DEFAULT_ARTIFACT_HANDLER_DEPS = ArtifactHandlerDeps()


def handle_submit_artifact(
    session: SessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    require_capability(session, ARTIFACT_SUBMIT_CAPABILITY, "Artifact submission")
    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS
    artifact_type, parsed_content = _prepare_artifact_submission(
        params,
        base_path=_workspace_root(workspace),
        backend=resolved_deps.backend,
    )

    artifact_dir = _artifact_dir(workspace)
    try:
        _run_pre_submit_side_effect(artifact_type, workspace, parsed_content, deps=resolved_deps)
        submit_artifact(
            artifact_dir,
            name=artifact_type,
            artifact_type=artifact_type,
            content=parsed_content,
            options=ArtifactSubmitOptions(
                overwrite=True,
                persistence=resolved_deps.artifact_persistence,
            ),
        )
    except Exception:
        _rollback_submit_side_effect(artifact_type, workspace, artifact_dir, deps=resolved_deps)
        raise

    _run_post_submit_side_effect(artifact_type, artifact_dir, deps=resolved_deps)
    return ToolResult(
        content=[ToolContent.text_content(f"Artifact submitted: {artifact_type}")],
        is_error=False,
    )


def handle_submit_plan_section(
    session: SessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
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

    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS

    try:
        fragment = validate_plan_section(section, payload, mode=mode)
    except PlanArtifactValidationError as exc:
        raise InvalidParamsError(f"[{section}] {exc}") from exc

    artifact_dir = _artifact_dir(workspace)
    draft = load_plan_draft(artifact_dir, backend=resolved_deps.backend) or new_plan_draft(
        now_iso=resolved_deps.now_iso
    )
    current_sections = cast("dict[str, object]", draft.get("sections", {}))
    updated_sections = merge_plan_section(current_sections, section, fragment, mode)
    draft["sections"] = updated_sections
    save_plan_draft(
        artifact_dir,
        draft,
        backend=resolved_deps.backend,
        now_iso=resolved_deps.now_iso,
    )

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
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Validate the staged draft as a whole plan and write plan.json."""
    require_capability(session, ARTIFACT_SUBMIT_CAPABILITY, "Plan finalization")
    del params  # no params
    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS

    artifact_dir = _artifact_dir(workspace)
    draft = load_plan_draft(artifact_dir, backend=resolved_deps.backend)
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
        options=ArtifactSubmitOptions(
            overwrite=True,
            persistence=resolved_deps.artifact_persistence,
        ),
    )
    delete_plan_draft(artifact_dir, backend=resolved_deps.backend)

    return ToolResult(
        content=[ToolContent.text_content(f"Artifact submitted: {PLAN_ARTIFACT_TYPE}")],
        is_error=False,
    )


def handle_get_plan_draft(
    session: SessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Return the current plan draft so an agent can resume after a restart."""
    require_capability(session, ARTIFACT_SUBMIT_CAPABILITY, "Plan draft read")
    del params
    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS

    artifact_dir = _artifact_dir(workspace)
    draft = load_plan_draft(artifact_dir, backend=resolved_deps.backend)
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
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Delete the on-disk plan draft so the agent can start over."""
    require_capability(session, ARTIFACT_SUBMIT_CAPABILITY, "Plan draft discard")
    del params
    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS

    existed = delete_plan_draft(_artifact_dir(workspace), backend=resolved_deps.backend)
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


CanonicalArtifactType = Literal["commit_message", "plan", "development_result"]


def _prepare_artifact_submission(
    params: dict[str, object],
    *,
    base_path: Path | None = None,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> tuple[str, dict[str, object]]:
    artifact_type = _canonical_artifact_type(_required_string(params, "artifact_type"))
    raw_content = _resolve_artifact_content_source(params, base_path=base_path, backend=backend)
    parsed_content = _unwrap_persisted_artifact_payload(artifact_type, _parse_content(raw_content))

    return artifact_type, _normalize_artifact_payload(artifact_type, parsed_content)


def _resolve_artifact_content_source(
    params: dict[str, object], *, base_path: Path | None, backend: FileBackend
) -> str:
    raw_content = params.get("content")
    raw_content_path = params.get("content_path")
    has_content = isinstance(raw_content, str)
    has_content_path = isinstance(raw_content_path, str)

    if has_content == has_content_path:
        raise InvalidParamsError("Provide exactly one of 'content' or 'content_path'")

    if has_content:
        return cast("str", raw_content)

    content_path = _resolve_content_path(cast("str", raw_content_path), base_path=base_path)
    try:
        return backend.read_text(content_path)
    except FileNotFoundError as exc:
        raise InvalidParamsError(f"Content file does not exist: {content_path}") from exc
    except OSError as exc:
        raise InvalidParamsError(f"Failed to read content file '{content_path}': {exc}") from exc


def _resolve_content_path(raw_path: str, *, base_path: Path | None) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute() or base_path is None:
        return candidate
    return (base_path / candidate).resolve()


def _unwrap_persisted_artifact_payload(
    artifact_type: str, parsed_content: dict[str, object]
) -> dict[str, object]:
    persisted_type = parsed_content.get("type")
    persisted_content = parsed_content.get("content")
    if persisted_type == artifact_type and isinstance(persisted_content, dict):
        return cast("dict[str, object]", persisted_content)
    return parsed_content


def _canonical_artifact_type(artifact_type: str) -> str:
    if artifact_type in {"commit", "skip"}:
        return COMMIT_MESSAGE_TYPE
    return artifact_type


def _normalize_artifact_payload(
    artifact_type: str, parsed_content: dict[str, object]
) -> dict[str, object]:
    if artifact_type == COMMIT_MESSAGE_TYPE:
        return _normalize_commit_message_payload(parsed_content)
    if artifact_type == PLAN_ARTIFACT_TYPE:
        return _normalize_plan_payload(parsed_content)
    if artifact_type == DEVELOPMENT_RESULT_ARTIFACT_TYPE:
        return _normalize_development_result_payload(parsed_content)
    return parsed_content


def _normalize_commit_message_payload(parsed_content: dict[str, object]) -> dict[str, object]:
    if "message" in parsed_content:
        raise InvalidParamsError(
            "commit_message artifacts must use the structured commit_message schema; "
            "legacy 'message' payloads are no longer accepted"
        )
    try:
        return normalize_commit_message_content(parsed_content)
    except ValueError as exc:
        raise InvalidParamsError(str(exc)) from exc


def _normalize_plan_payload(parsed_content: dict[str, object]) -> dict[str, object]:
    try:
        return normalize_plan_artifact_content(parsed_content)
    except PlanArtifactValidationError as exc:
        raise InvalidParamsError(str(exc)) from exc


def _normalize_development_result_payload(parsed_content: dict[str, object]) -> dict[str, object]:
    try:
        return normalize_development_result_content(parsed_content)
    except DevelopmentResultValidationError as exc:
        raise InvalidParamsError(str(exc)) from exc


def _run_pre_submit_side_effect(
    artifact_type: str,
    workspace: WorkspaceLike,
    parsed_content: dict[str, object],
    *,
    deps: ArtifactHandlerDeps,
) -> None:
    if artifact_type == COMMIT_MESSAGE_TYPE:
        write_commit_message_artifact(
            _workspace_root(workspace),
            parsed_content,
            backend=deps.backend,
            now_iso=deps.now_iso,
        )


def _rollback_submit_side_effect(
    artifact_type: str,
    workspace: WorkspaceLike,
    artifact_dir: Path,
    *,
    deps: ArtifactHandlerDeps,
) -> None:
    if artifact_type == COMMIT_MESSAGE_TYPE:
        delete_commit_message_artifacts(_workspace_root(workspace), backend=deps.backend)
        with suppress(Exception):
            delete_artifact(artifact_dir, artifact_type, backend=deps.backend)


def _run_post_submit_side_effect(
    artifact_type: str, artifact_dir: Path, *, deps: ArtifactHandlerDeps
) -> None:
    if artifact_type == PLAN_ARTIFACT_TYPE:
        # Atomic full-plan submission supersedes any partial draft.
        delete_plan_draft(artifact_dir, backend=deps.backend)


__all__ = [
    "ArtifactHandlerDeps",
    "_prepare_artifact_submission",
    "handle_discard_plan_draft",
    "handle_finalize_plan",
    "handle_get_plan_draft",
    "handle_submit_artifact",
    "handle_submit_plan_section",
]
