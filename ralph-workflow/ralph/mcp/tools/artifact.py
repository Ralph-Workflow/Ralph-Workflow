"""MCP artifact submission handlers."""

from __future__ import annotations

import json
import tomllib
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NoReturn, cast

from ralph.mcp.artifacts._artifact_persistence import (
    DEFAULT_ARTIFACT_PERSISTENCE,
    ArtifactPersistence,
)
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
from ralph.mcp.artifacts.format_docs import (
    has_format_doc,
    materialize_format_doc,
    materialize_format_index,
)
from ralph.mcp.artifacts.handoffs import delete_markdown_handoff, sync_markdown_handoff
from ralph.mcp.artifacts.history import rebuild_history_index, snapshot_current_artifact
from ralph.mcp.artifacts.plan import (
    PLAN_ARTIFACT_TYPE,
    PLAN_SECTION_NAMES,
    PlanArtifactValidationError,
    SectionMode,
    delete_plan_draft,
    finalize_plan_draft,
    load_plan_artifact_sections,
    load_plan_draft,
    merge_plan_section,
    new_plan_draft,
    normalize_plan_artifact_content,
    save_plan_draft,
    validate_plan_section,
)
from ralph.mcp.artifacts.product_spec import (
    PRODUCT_SPEC_ARTIFACT_TYPE,
    ProductSpecValidationError,
    normalize_product_spec_content,
)
from ralph.mcp.artifacts.smoke_test_result import (
    SMOKE_TEST_RESULT_ARTIFACT_TYPE,
    SmokeTestResultValidationError,
    normalize_smoke_test_result_content,
)
from ralph.mcp.artifacts.store import (
    ArtifactSubmitOptions,
    delete_artifact,
    submit_artifact,
)
from ralph.mcp.artifacts.typed_artifacts import (
    TypedArtifactValidationError,
    normalize_analysis_decision_content,
    normalize_commit_cleanup_content,
    normalize_fix_result_content,
    normalize_issues_content,
)
from ralph.mcp.tools._submit_op import SubmitOp
from ralph.mcp.tools.coordination import (
    ARTIFACT_SUBMIT_CAPABILITY,
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    ToolResult,
    WorkspaceLike,
    require_capability,
)
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.policy.models import PipelinePolicy

PLAN_DRAFT_READ_CAPABILITY = "artifact.plan_read"
PLAN_DRAFT_WRITE_CAPABILITY = "artifact.plan_write"


_TYPED_ARTIFACT_TYPES = frozenset(
    {
        "commit_cleanup",
        "issues",
        "fix_result",
        "development_analysis_decision",
        "planning_analysis_decision",
        "review_analysis_decision",
        SMOKE_TEST_RESULT_ARTIFACT_TYPE,
        PRODUCT_SPEC_ARTIFACT_TYPE,
    }
)

_KNOWN_ARTIFACT_TYPES = frozenset(
    {PLAN_ARTIFACT_TYPE, COMMIT_MESSAGE_TYPE, DEVELOPMENT_RESULT_ARTIFACT_TYPE}
    | _TYPED_ARTIFACT_TYPES
)


def execute_ops_with_rollback(ops: list[SubmitOp]) -> None:
    """Execute a sequence of ops; on failure roll back all completed ops in reverse."""
    completed: list[SubmitOp] = []
    try:
        for op in ops:
            op.run()
            completed.append(op)
    except Exception:
        for completed_op in reversed(completed):
            with suppress(Exception):
                completed_op.undo()
        raise


def _noop_now_iso() -> str:
    return DEFAULT_ARTIFACT_PERSISTENCE.now_iso()


@dataclass(frozen=True)
class ArtifactHandlerDeps:
    """Injectable dependencies for artifact handler operations."""

    backend: FileBackend = DEFAULT_FILE_BACKEND
    now_iso: Callable[[], str] = _noop_now_iso
    history_enabled: bool = False

    @property
    def artifact_persistence(self) -> ArtifactPersistence:
        return ArtifactPersistence(backend=self.backend, now_iso=self.now_iso)


DEFAULT_ARTIFACT_HANDLER_DEPS = ArtifactHandlerDeps()


def _resolve_artifact_dir(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
) -> Path:
    """Resolve the artifact directory for a session.

    For parallel workers with a per-worker artifact directory set on the session,
    use that directory instead of the shared workspace artifact path.
    This ensures worker artifacts are namespaced under .agent/workers/<unit_id>/artifacts/
    and do not collide with the parent process or other workers.
    """
    # Check if the session has a worker-specific artifact directory set.
    # This is set for parallel workers via AgentSession.worker_artifact_dir.
    worker_artifacts = cast("Path | None", getattr(session, "worker_artifact_dir", None))
    if worker_artifacts is not None:
        return worker_artifacts
    return _artifact_dir(workspace)


def _resolve_history_enabled(
    artifact_type: str,
    workspace_root: Path,
    drain: str | None,
) -> bool:
    """Check whether artifact history is enabled for the given drain.

    Loads the active policy bundle from workspace_root / '.agent' and inspects
    the artifact_history.enabled flag on phases matching the drain. Returns False
    on any load or validation error so history is always opt-in.
    """
    try:
        bundle = load_policy(workspace_root / ".agent")
        for phase_def in bundle.pipeline.phases.values():
            if drain is not None and phase_def.drain != drain:
                continue
            if phase_def.artifact_history is not None and phase_def.artifact_history.enabled:
                return True
    except Exception:  # policy load errors must not surface to agents
        pass
    return False


def handle_submit_artifact(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Validate and persist an artifact submitted by an MCP agent."""
    require_capability(session, ARTIFACT_SUBMIT_CAPABILITY, "Artifact submission")
    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS
    drain = _session_drain(session)
    workspace_root = _workspace_root(workspace)
    artifact_type, parsed_content = prepare_artifact_submission(
        params,
        session_drain=drain,
        base_path=workspace_root,
        backend=resolved_deps.backend,
    )

    artifact_dir = _resolve_artifact_dir(session, workspace)
    history_enabled = _resolve_history_enabled(artifact_type, workspace_root, drain)
    effective_deps = ArtifactHandlerDeps(
        backend=resolved_deps.backend,
        now_iso=resolved_deps.now_iso,
        history_enabled=history_enabled,
    )
    execute_ops_with_rollback(
        submit_ops_for_artifact(
            artifact_type,
            workspace_root,
            artifact_dir,
            parsed_content,
            deps=effective_deps,
        )
    )

    return ToolResult(
        content=[ToolContent.text_content(f"Artifact submitted: {artifact_type}")],
        is_error=False,
    )


def handle_submit_plan_section(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Validate a single plan section and merge it into the on-disk draft."""
    require_capability(session, PLAN_DRAFT_WRITE_CAPABILITY, "Plan section submission")

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

    artifact_dir = _resolve_artifact_dir(session, workspace)
    draft = load_plan_draft(artifact_dir, backend=resolved_deps.backend)
    if draft is None:
        hydrated_sections = load_plan_artifact_sections(artifact_dir, backend=resolved_deps.backend)
        draft = new_plan_draft(now_iso=resolved_deps.now_iso)
        if hydrated_sections is not None:
            draft["sections"] = hydrated_sections
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
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Validate the staged draft as a whole plan and write plan.json."""
    require_capability(session, PLAN_DRAFT_WRITE_CAPABILITY, "Plan finalization")
    del params
    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS

    artifact_dir = _resolve_artifact_dir(session, workspace)
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

    # Keep the structured JSON artifact for Ralph's validation/routing, but
    # always mirror agent/user-consumed artifacts into Markdown handoffs so
    # downstream phases never need to read raw JSON directly.
    workspace_root = _workspace_root(workspace)
    drain = _session_drain(session)
    history_enabled = _resolve_history_enabled(PLAN_ARTIFACT_TYPE, workspace_root, drain)
    effective_deps = ArtifactHandlerDeps(
        backend=resolved_deps.backend,
        now_iso=resolved_deps.now_iso,
        history_enabled=history_enabled,
    )
    execute_ops_with_rollback(
        submit_ops_for_artifact(
            PLAN_ARTIFACT_TYPE,
            workspace_root,
            artifact_dir,
            normalized,
            deps=effective_deps,
        )
    )

    return ToolResult(
        content=[ToolContent.text_content(f"Artifact submitted: {PLAN_ARTIFACT_TYPE}")],
        is_error=False,
    )


def _iso_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    with suppress(ValueError):
        return datetime.fromisoformat(value)
    return None


def _load_finalized_plan_response(
    artifact_dir: Path,
    *,
    backend: FileBackend,
) -> dict[str, object] | None:
    hydrated_sections = load_plan_artifact_sections(artifact_dir, backend=backend)
    if hydrated_sections is None:
        return None
    updated_at: object = None
    with suppress(OSError, json.JSONDecodeError):
        raw = backend.read_text(artifact_dir / "plan.json", encoding="utf-8")
        parsed = cast("object", json.loads(raw))
        if isinstance(parsed, dict):
            updated_at = parsed.get("updated_at")
    return {
        "staged_sections": sorted(hydrated_sections.keys()),
        "draft": hydrated_sections,
        "source": "finalized_plan",
        "updated_at": updated_at,
    }


def _current_plan_draft_response(
    artifact_dir: Path,
    *,
    backend: FileBackend,
) -> dict[str, object]:
    draft = load_plan_draft(artifact_dir, backend=backend)
    finalized_response = _load_finalized_plan_response(artifact_dir, backend=backend)
    if draft is None:
        if finalized_response is None:
            return {"staged_sections": []}
        finalized_response.pop("updated_at", None)
        return finalized_response

    draft_updated_at = _iso_timestamp(draft.get("updated_at"))
    finalized_updated_at = (
        _iso_timestamp(finalized_response.get("updated_at"))
        if finalized_response is not None
        else None
    )
    if (
        finalized_response is not None
        and finalized_updated_at is not None
        and draft_updated_at is not None
        and finalized_updated_at >= draft_updated_at
    ):
        finalized_response.pop("updated_at", None)
        return finalized_response

    sections = cast("dict[str, object]", draft.get("sections", {}))
    return {
        "staged_sections": sorted(sections.keys()),
        "started_at": draft.get("started_at"),
        "updated_at": draft.get("updated_at"),
        "draft": sections,
        "source": "draft",
    }


def handle_get_plan_draft(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Return the current plan draft so an agent can resume after a restart."""
    require_capability(session, PLAN_DRAFT_READ_CAPABILITY, "Plan draft read")
    del params
    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS

    artifact_dir = _resolve_artifact_dir(session, workspace)
    response = _current_plan_draft_response(
        artifact_dir,
        backend=resolved_deps.backend,
    )

    return ToolResult(
        content=[ToolContent.text_content(json.dumps(response))],
        is_error=False,
    )


def handle_discard_plan_draft(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Delete the on-disk plan draft so the agent can start over."""
    require_capability(session, PLAN_DRAFT_WRITE_CAPABILITY, "Plan draft discard")
    del params
    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS

    existed = delete_plan_draft(
        _resolve_artifact_dir(session, workspace), backend=resolved_deps.backend
    )
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


def _raise_index_format_error(
    workspace_root: Path,
    backend: FileBackend,
    reason: str,
) -> NoReturn:
    """Raise an InvalidParamsError pointing to the artifact formats index."""
    materialize_format_index(workspace_root, backend=backend)
    raise InvalidParamsError(
        f"{reason} Read '.agent/artifact-formats/artifact_formats_index.md' inside the workspace "
        "for the list of valid artifact_type values and how to submit each one. "
        "Do NOT rely on the raw error text."
    ) from None


def prepare_artifact_submission(
    params: dict[str, object],
    *,
    session_drain: str | None = None,
    base_path: Path | None = None,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> tuple[str, dict[str, object]]:
    """Validate and canonicalize artifact submission params, returning (artifact_type, params)."""
    # Handle missing artifact_type
    try:
        raw_artifact_type = _required_string(params, "artifact_type")
    except InvalidParamsError:
        if base_path is not None:
            _raise_index_format_error(
                base_path,
                backend,
                "Missing required field artifact_type.",
            )
        raise

    # Canonicalize artifact_type (handles aliases like "commit", "analysis_decision")
    canonical_exc = None
    artifact_type: str | None = None
    try:
        artifact_type = _canonical_artifact_type(
            raw_artifact_type,
            session_drain=session_drain,
        )
    except InvalidParamsError as exc:
        canonical_exc = exc

    if canonical_exc is not None:
        if base_path is not None:
            if raw_artifact_type == "analysis_decision":
                # Materialize the relevant docs and index before raising
                materialize_format_doc(base_path, "development_analysis_decision", backend=backend)
                materialize_format_doc(base_path, "planning_analysis_decision", backend=backend)
                materialize_format_doc(base_path, "review_analysis_decision", backend=backend)
                materialize_format_index(base_path, backend=backend)
                raise InvalidParamsError(
                    "artifact_type 'analysis_decision' can only be used inside "
                    "an analysis drain session such as development_analysis, "
                    "planning_analysis, or review_analysis. Submit the "
                    "drain-specific '*_analysis_decision' artifact type "
                    "directly instead when needed. Read "
                    "'.agent/artifact-formats/artifact_formats_index.md' "
                    "for the full list of valid artifact_type values. "
                    "Do NOT rely on the raw error text."
                ) from None
            elif raw_artifact_type not in _KNOWN_ARTIFACT_TYPES:
                # Unknown artifact type - redirect to index
                _raise_index_format_error(
                    base_path,
                    backend,
                    f"Unknown artifact_type {raw_artifact_type!r}.",
                )
        raise canonical_exc

    if artifact_type is None:
        raise canonical_exc or InvalidParamsError("Missing 'artifact_type' parameter")

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
    params: dict[str, object],
    *,
    artifact_type: str,
    base_path: Path | None,
    backend: FileBackend,
) -> str:
    raw_content = params.get("content")
    raw_content_path = params.get("content_path")

    if isinstance(raw_content_path, str):
        exc = InvalidParamsError(_artifact_content_format_error(artifact_type))
        if (
            base_path is not None
            and artifact_type != PLAN_ARTIFACT_TYPE
            and has_format_doc(artifact_type)
        ):
            _raise_format_doc_error(artifact_type, base_path, backend, exc)
        raise exc

    if not isinstance(raw_content, str):
        exc = InvalidParamsError("Missing 'content' parameter")
        if (
            base_path is not None
            and artifact_type != PLAN_ARTIFACT_TYPE
            and has_format_doc(artifact_type)
        ):
            _raise_format_doc_error(artifact_type, base_path, backend, exc)
        raise exc

    return raw_content


def _artifact_content_format_error(artifact_type: str) -> str:
    fresh_submit_example = (
        f'{{"artifact_type":"{artifact_type}",'
        '"content":"{\\"status\\":\\"completed\\",\\"summary\\":\\"...\\"}"}'
    )
    return (
        "Artifact submission requires the 'content' field. "
        "Use 'content' with a freshly generated JSON string. "
        "Do not use 'content_path' in agent-facing artifact submissions. "
        f"Example submit: {fresh_submit_example}."
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


def _canonical_artifact_type(
    artifact_type: str,
    *,
    session_drain: str | None = None,
    pipeline_policy: PipelinePolicy | None = None,
) -> str:
    if artifact_type in {"commit", "skip"}:
        return COMMIT_MESSAGE_TYPE
    if artifact_type == "analysis_decision":
        return _analysis_decision_artifact_type(session_drain, pipeline_policy=pipeline_policy)
    if artifact_type not in _KNOWN_ARTIFACT_TYPES:
        raise InvalidParamsError(f"Unknown artifact_type {artifact_type!r}.")
    return artifact_type


def _analysis_decision_artifact_type(
    session_drain: str | None,
    *,
    pipeline_policy: PipelinePolicy | None = None,
) -> str:
    """Derive the canonical artifact type for an analysis-drain session.

    When pipeline_policy is provided, the type is derived generically as
    '{session_drain}_decision' for any drain bound to a phase with role='analysis'.
    Without a policy, falls back to '{session_drain}_decision' only when the drain
    name ends with '_analysis' (naming convention).

    The previously hardcoded mapping (development_analysis → development_analysis_decision,
    review_analysis → review_analysis_decision) has been removed.
    """
    if session_drain is None:
        raise InvalidParamsError("analysis_decision requires an analysis drain session")
    if pipeline_policy is not None:
        for phase_def in pipeline_policy.phases.values():
            if phase_def.drain == session_drain and phase_def.role == "analysis":
                return f"{session_drain}_decision"
    # Conservative fallback: naming-convention suffix
    if session_drain.endswith("_analysis"):
        return f"{session_drain}_decision"
    raise InvalidParamsError("analysis_decision requires an analysis drain session")


def _accepted_persisted_types(artifact_type: str) -> set[str]:
    accepted = {artifact_type}
    if artifact_type.endswith("_decision"):
        accepted.add("analysis_decision")
    return accepted


def _session_drain(session: CoordinationSessionLike) -> str | None:
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
    if artifact_type in _TYPED_ARTIFACT_TYPES:
        return _normalize_typed_artifact_payload(
            artifact_type, parsed_content, workspace_root=workspace_root, backend=backend
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


def _analysis_decision_vocabulary_for_artifact_type(
    artifact_type: str,
    *,
    workspace_root: Path | None,
) -> frozenset[str] | None:
    if workspace_root is None:
        return None

    artifacts_path = workspace_root / ".agent" / "artifacts.toml"
    if not artifacts_path.exists():
        return None

    try:
        with artifacts_path.open("rb") as f:
            data = cast("dict[str, object]", tomllib.load(f))
    except (OSError, ValueError):
        return None

    artifacts_obj = data.get("artifacts")
    if not isinstance(artifacts_obj, dict):
        return None

    drain_name = artifact_type.removesuffix("_decision")
    for contract_obj in artifacts_obj.values():
        if not isinstance(contract_obj, dict):
            continue
        contract = cast("dict[str, object]", contract_obj)
        if contract.get("drain") != drain_name:
            continue
        if contract.get("artifact_type") != artifact_type:
            continue
        raw_vocab = contract.get("decision_vocabulary")
        if isinstance(raw_vocab, list):
            return frozenset(str(v) for v in raw_vocab)
    return None


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
    workspace_root: Path | None = None,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> dict[str, object]:
    try:
        if artifact_type == "commit_cleanup":
            return normalize_commit_cleanup_content(parsed_content)
        if artifact_type == "issues":
            return normalize_issues_content(parsed_content)
        if artifact_type == "fix_result":
            return normalize_fix_result_content(parsed_content)
        if artifact_type == SMOKE_TEST_RESULT_ARTIFACT_TYPE:
            return normalize_smoke_test_result_content(parsed_content)
        if artifact_type == PRODUCT_SPEC_ARTIFACT_TYPE:
            return normalize_product_spec_content(parsed_content)
        allowed_statuses = _analysis_decision_vocabulary_for_artifact_type(
            artifact_type,
            workspace_root=workspace_root,
        )
        return normalize_analysis_decision_content(
            parsed_content,
            allowed_statuses=allowed_statuses,
        )
    except (
        TypedArtifactValidationError,
        SmokeTestResultValidationError,
        ProductSpecValidationError,
    ) as exc:
        if workspace_root is not None:
            _raise_format_doc_error(artifact_type, workspace_root, backend, exc)
        raise InvalidParamsError(str(exc)) from exc


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


def submit_ops_for_artifact(
    artifact_type: str,
    workspace_root: Path,
    artifact_dir: Path,
    parsed_content: dict[str, object],
    *,
    deps: ArtifactHandlerDeps,
) -> list[SubmitOp]:
    """Return the ordered (op, undo) pairs for a complete artifact submit."""
    ops: list[SubmitOp] = []

    if artifact_type == COMMIT_MESSAGE_TYPE:
        _content = parsed_content
        ops.append(
            SubmitOp(
                run=lambda: write_commit_message_artifact(
                    workspace_root, _content, backend=deps.backend, now_iso=deps.now_iso
                ),
                undo=lambda: delete_commit_message_artifacts(workspace_root, backend=deps.backend),
            )
        )

    _options = ArtifactSubmitOptions(overwrite=True, persistence=deps.artifact_persistence)
    _at = artifact_type
    _content2 = parsed_content
    ops.append(
        SubmitOp(
            run=lambda: submit_artifact(
                artifact_dir,
                name=_at,
                artifact_type=_at,
                content=_content2,
                options=_options,
            ),
            undo=lambda: delete_artifact(artifact_dir, _at, backend=deps.backend),
        )
    )

    _content3 = parsed_content
    ops.append(
        SubmitOp(
            run=lambda: sync_markdown_handoff(workspace_root, _at, _content3, backend=deps.backend),
            undo=lambda: delete_markdown_handoff(workspace_root, _at, backend=deps.backend),
        )
    )

    if deps.history_enabled:
        _snapshotted_paths: list[Path] = []
        _at_hist = artifact_type
        _wr = workspace_root
        _ad = artifact_dir

        def _run_history_snapshot() -> None:
            paths = snapshot_current_artifact(
                _ad,
                _wr,
                _at_hist,
                backend=deps.backend,
                now_iso=deps.now_iso,
            )
            _snapshotted_paths.extend(paths)

        def _undo_history_snapshot() -> None:
            for path in _snapshotted_paths:
                deps.backend.unlink(path, missing_ok=True)
            rebuild_history_index(_ad, _at_hist, backend=deps.backend)

        ops.append(SubmitOp(run=_run_history_snapshot, undo=_undo_history_snapshot))

    if artifact_type == PLAN_ARTIFACT_TYPE:
        ops.append(
            SubmitOp(
                run=lambda: delete_plan_draft(artifact_dir, backend=deps.backend),
                undo=lambda: None,
            )
        )

    return ops


__all__ = [
    "ArtifactHandlerDeps",
    "SubmitOp",
    "_resolve_history_enabled",
    "execute_ops_with_rollback",
    "handle_discard_plan_draft",
    "handle_finalize_plan",
    "handle_get_plan_draft",
    "handle_submit_artifact",
    "handle_submit_plan_section",
    "prepare_artifact_submission",
    "submit_ops_for_artifact",
]
