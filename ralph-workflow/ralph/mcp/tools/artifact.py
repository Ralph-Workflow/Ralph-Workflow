"""MCP artifact submission handlers."""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NoReturn, cast

from ralph.mcp.artifacts.commit_message import (
    COMMIT_MESSAGE_TYPE,
    delete_commit_message_artifacts,
    normalize_commit_message_content,
    write_commit_message_artifact,
)
from ralph.mcp.artifacts.development_result import (
    DEVELOPMENT_RESULT_ARTIFACT_TYPE,
    DevelopmentResultValidationError,
    normalize_development_result_content,
)
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.format_docs import has_format_doc, materialize_format_doc
from ralph.mcp.artifacts.handoffs import delete_markdown_handoff, sync_markdown_handoff
from ralph.mcp.artifacts.plan import (
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
from ralph.mcp.artifacts.store import (
    DEFAULT_ARTIFACT_PERSISTENCE,
    ArtifactPersistence,
    ArtifactSubmitOptions,
    delete_artifact,
    submit_artifact,
)
from ralph.mcp.tools.coordination import (
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
        session_drain=_session_drain(session),
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
        _run_post_submit_side_effect(
            artifact_type,
            workspace,
            artifact_dir,
            parsed_content,
            deps=resolved_deps,
        )
    except Exception:
        _rollback_submit_side_effect(artifact_type, workspace, artifact_dir, deps=resolved_deps)
        raise

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

    try:
        # Keep the structured JSON artifact for Ralph's validation/routing, but
        # always mirror agent/user-consumed artifacts into Markdown handoffs so
        # downstream phases never need to read raw JSON directly.
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
        sync_markdown_handoff(
            _workspace_root(workspace),
            PLAN_ARTIFACT_TYPE,
            normalized,
            backend=resolved_deps.backend,
        )
        delete_plan_draft(artifact_dir, backend=resolved_deps.backend)
    except Exception:
        with suppress(Exception):
            delete_artifact(artifact_dir, PLAN_ARTIFACT_TYPE, backend=resolved_deps.backend)
        raise

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

# Required fields for artifact types that have no dedicated Pydantic normalizer.
_TYPED_ARTIFACT_REQUIRED_FIELDS: dict[str, list[str]] = {
    "issues": ["status", "summary", "issues", "what_came_up_short", "how_to_fix"],
    "fix_result": ["summary", "files_changed"],
    "development_analysis_decision": ["status", "summary", "what_came_up_short", "how_to_fix"],
    "review_analysis_decision": ["status", "summary", "what_came_up_short", "how_to_fix"],
}


def _prepare_artifact_submission(
    params: dict[str, object],
    *,
    session_drain: str | None = None,
    base_path: Path | None = None,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> tuple[str, dict[str, object]]:
    artifact_type = _canonical_artifact_type(
        _required_string(params, "artifact_type"),
        session_drain=session_drain,
    )
    raw_content = _resolve_artifact_content_source(
        params,
        artifact_type=artifact_type,
        base_path=base_path,
        backend=backend,
    )

    try:
        parsed_content = _unwrap_persisted_artifact_payload(
            artifact_type, _parse_content(raw_content)
        )
    except InvalidParamsError as exc:
        if (
            base_path is not None
            and artifact_type != PLAN_ARTIFACT_TYPE
            and has_format_doc(artifact_type)
        ):
            _raise_format_doc_error(artifact_type, base_path, backend, exc)
        raise

    return artifact_type, _normalize_artifact_payload(
        artifact_type, parsed_content, workspace_root=base_path, backend=backend
    )


def _resolve_artifact_content_source(
    params: dict[str, object], *, artifact_type: str, base_path: Path | None, backend: FileBackend
) -> str:
    raw_content = params.get("content")
    raw_content_path = params.get("content_path")
    has_content = isinstance(raw_content, str)
    has_content_path = isinstance(raw_content_path, str)

    if has_content == has_content_path:
        raise InvalidParamsError(_artifact_content_format_error(artifact_type))

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


def _artifact_content_format_error(artifact_type: str) -> str:
    fresh_submit_example = (
        f'{{"artifact_type":"{artifact_type}",'
        '"content":"{\\"status\\":\\"completed\\",\\"summary\\":\\"...\\"}"}'
    )
    resubmit_example = (
        f'{{"artifact_type":"{artifact_type}",'
        f'"content_path":".agent/artifacts/{artifact_type}.json"}}'
    )
    return (
        "Provide exactly one of 'content' or 'content_path'. "
        "Use 'content' for a freshly generated JSON string. "
        "Use 'content_path' only when resubmitting a JSON file that already exists on disk. "
        "Never send both 'content' and 'content_path' in the same call. "
        f"Example fresh submit: {fresh_submit_example}. "
        f"Example resubmit from disk: {resubmit_example}."
    )


def _unwrap_persisted_artifact_payload(
    artifact_type: str, parsed_content: dict[str, object]
) -> dict[str, object]:
    persisted_type = parsed_content.get("type")
    persisted_content = parsed_content.get("content")
    if persisted_type in _accepted_persisted_types(artifact_type) and isinstance(
        persisted_content, dict
    ):
        return cast("dict[str, object]", persisted_content)
    return parsed_content


def _canonical_artifact_type(artifact_type: str, *, session_drain: str | None = None) -> str:
    if artifact_type in {"commit", "skip"}:
        return COMMIT_MESSAGE_TYPE
    if artifact_type == "analysis_decision":
        return _analysis_decision_artifact_type(session_drain)
    return artifact_type


def _analysis_decision_artifact_type(session_drain: str | None) -> str:
    mapping = {
        "development_analysis": "development_analysis_decision",
        "review_analysis": "review_analysis_decision",
    }
    if session_drain in mapping:
        return mapping[session_drain]
    raise InvalidParamsError("analysis_decision requires an analysis drain session")


def _accepted_persisted_types(artifact_type: str) -> set[str]:
    accepted = {artifact_type}
    if artifact_type in {"development_analysis_decision", "review_analysis_decision"}:
        accepted.add("analysis_decision")
    return accepted


def _session_drain(session: SessionLike) -> str | None:
    try:
        attributes = cast("dict[str, object]", vars(session))
    except TypeError:
        return None
    drain = attributes.get("drain")
    return drain if isinstance(drain, str) else None


def _normalize_artifact_payload(
    artifact_type: str,
    parsed_content: dict[str, object],
    *,
    workspace_root: Path | None = None,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> dict[str, object]:
    if artifact_type == COMMIT_MESSAGE_TYPE:
        return _normalize_commit_message_payload(
            parsed_content, workspace_root=workspace_root, backend=backend
        )
    if artifact_type == PLAN_ARTIFACT_TYPE:
        return _normalize_plan_payload(parsed_content)
    if artifact_type == DEVELOPMENT_RESULT_ARTIFACT_TYPE:
        return _normalize_development_result_payload(
            parsed_content, workspace_root=workspace_root, backend=backend
        )
    required_fields = _TYPED_ARTIFACT_REQUIRED_FIELDS.get(artifact_type)
    if required_fields is not None:
        return _normalize_typed_artifact_payload(
            artifact_type,
            parsed_content,
            required_fields=required_fields,
            workspace_root=workspace_root,
            backend=backend,
        )
    return parsed_content


def _normalize_commit_message_payload(
    parsed_content: dict[str, object],
    *,
    workspace_root: Path | None = None,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> dict[str, object]:
    if "message" in parsed_content:
        exc = InvalidParamsError(
            "commit_message artifacts must use the structured commit_message schema; "
            "legacy 'message' payloads are no longer accepted"
        )
        if workspace_root is not None:
            _raise_format_doc_error(COMMIT_MESSAGE_TYPE, workspace_root, backend, exc)
        raise exc
    try:
        return normalize_commit_message_content(parsed_content)
    except ValueError as exc:
        if workspace_root is not None:
            _raise_format_doc_error(COMMIT_MESSAGE_TYPE, workspace_root, backend, exc)
        raise InvalidParamsError(str(exc)) from exc


def _normalize_plan_payload(parsed_content: dict[str, object]) -> dict[str, object]:
    try:
        return normalize_plan_artifact_content(parsed_content)
    except PlanArtifactValidationError as exc:
        raise InvalidParamsError(str(exc)) from exc


def _normalize_development_result_payload(
    parsed_content: dict[str, object],
    *,
    workspace_root: Path | None = None,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> dict[str, object]:
    try:
        return normalize_development_result_content(parsed_content)
    except DevelopmentResultValidationError as exc:
        if workspace_root is not None:
            _raise_format_doc_error(DEVELOPMENT_RESULT_ARTIFACT_TYPE, workspace_root, backend, exc)
        raise InvalidParamsError(str(exc)) from exc


def _normalize_typed_artifact_payload(
    artifact_type: str,
    parsed_content: dict[str, object],
    *,
    required_fields: list[str],
    workspace_root: Path | None = None,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> dict[str, object]:
    missing = [f for f in required_fields if f not in parsed_content]
    if missing:
        exc = InvalidParamsError(
            f"Artifact '{artifact_type}' is missing required fields: {', '.join(missing)}"
        )
        if workspace_root is not None:
            _raise_format_doc_error(artifact_type, workspace_root, backend, exc)
        raise exc
    return parsed_content


def _raise_format_doc_error(
    artifact_type: str,
    workspace_root: Path,
    backend: FileBackend,
    original_exc: Exception,
) -> NoReturn:
    try:
        relative_path = materialize_format_doc(workspace_root, artifact_type, backend=backend)
        if relative_path is not None:
            msg = (
                f"Artifact '{artifact_type}' failed validation. "
                f"The exact format is documented at '{relative_path}' inside the workspace. "
                "Read that file and rebuild your submission before retrying. "
                "Do NOT rely on the raw error text for format guidance."
            )
        else:
            msg = (
                f"Artifact '{artifact_type}' failed validation. "
                f"(note: could not write the reference file; "
                f"read ralph/mcp/artifacts/format_docs/{artifact_type}.md "
                "in the ralph package source instead)"
            )
    except OSError:
        msg = (
            f"Artifact '{artifact_type}' failed validation. "
            f"(note: could not write the reference file; "
            f"read ralph/mcp/artifacts/format_docs/{artifact_type}.md "
            "in the ralph package source instead)"
        )
    raise InvalidParamsError(msg) from original_exc


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
        return

    with suppress(Exception):
        delete_markdown_handoff(_workspace_root(workspace), artifact_type, backend=deps.backend)
    with suppress(Exception):
        delete_artifact(artifact_dir, artifact_type, backend=deps.backend)


def _run_post_submit_side_effect(
    artifact_type: str,
    workspace: WorkspaceLike,
    artifact_dir: Path,
    parsed_content: dict[str, object],
    *,
    deps: ArtifactHandlerDeps,
) -> None:
    sync_markdown_handoff(
        _workspace_root(workspace),
        artifact_type,
        parsed_content,
        backend=deps.backend,
    )
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
