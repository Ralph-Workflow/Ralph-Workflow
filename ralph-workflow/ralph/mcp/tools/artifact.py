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
from ralph.mcp.artifacts.completion_receipts import (
    delete_artifact_receipt,
    write_artifact_receipt,
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
    PLAN_SECTION_LIST_ITEM_MODELS,
    PLAN_SECTION_NAMES,
    PLAN_SECTION_OBJECT_MODELS,
    PlanArtifactValidationError,
    SectionMode,
    delete_plan_draft,
    finalize_plan_draft,
    load_plan_artifact_sections,
    load_plan_draft,
    merge_plan_section,
    new_plan_draft,
    normalize_plan_artifact_content,
    parse_plan_payload_strict,
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
from ralph.mcp.protocol.capability_mapping import Capability
from ralph.mcp.tools._submit_op import SubmitOp
from ralph.mcp.tools.coordination import (
    ARTIFACT_SUBMIT_CAPABILITY,
    COMPLETION_SENTINEL_RELPATHFMT,
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

PLAN_DRAFT_READ_CAPABILITY = Capability.ARTIFACT_PLAN_READ.value
PLAN_DRAFT_WRITE_CAPABILITY = Capability.ARTIFACT_PLAN_WRITE.value


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
    | {"review", "verification"}
)

# Multi-step planning artifact types: each phase has its own submit, and
# completion is the explicit ``finalize_plan`` / ``declare_complete`` call.
# These MUST be excluded from the auto-complete-on-submit path so a phase
# submit does not prematurely satisfy the gate.
_PLANNING_DECISION_ARTIFACT_TYPES: frozenset[str] = frozenset(
    {
        PLAN_ARTIFACT_TYPE,
        "development_analysis_decision",
        "planning_analysis_decision",
        "review_analysis_decision",
    }
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

    history_enabled = _resolve_history_enabled(artifact_type, workspace_root, drain)
    effective_deps = ArtifactHandlerDeps(
        backend=resolved_deps.backend,
        now_iso=resolved_deps.now_iso,
        history_enabled=history_enabled,
    )
    artifact_dir = _resolve_artifact_dir(session, workspace)
    # === BEGIN CANONICAL SUBMIT OPS ===
    from ralph.mcp.artifacts.canonical_submit import (  # noqa: PLC0415
        submit_artifact_canonical,
    )

    submit_artifact_canonical(
        workspace_root=workspace_root,
        artifact_type=artifact_type,
        parsed_content=parsed_content,
        deps=effective_deps,
        run_id=_session_run_id(session),
        artifact_dir=artifact_dir,
    )
    # === END CANONICAL SUBMIT OPS ===

    # Post-submission verification: development_result artifacts require status="completed"
    # or status="partial" (from instructions). If status is neither, surface a verification
    # error after successful submission to direct the agent to complete work first.
    if artifact_type == DEVELOPMENT_RESULT_ARTIFACT_TYPE:
        status = parsed_content.get("status")
        if status not in ("completed", "partial"):
            return ToolResult(
                content=[
                    ToolContent.text_content(
                        "VERIFICATION ERROR: work must be completed before submitting "
                        "completion artifact. Partial status is not allowed."
                    )
                ],
                is_error=True,
            )

    return ToolResult(
        content=[ToolContent.text_content(f"Artifact submitted: {artifact_type}")],
        is_error=False,
    )


def _load_or_create_plan_draft(
    artifact_dir: Path,
    *,
    deps: ArtifactHandlerDeps,
) -> dict[str, object]:
    draft = load_plan_draft(artifact_dir, backend=deps.backend)
    if draft is not None:
        return draft

    hydrated_sections = load_plan_artifact_sections(artifact_dir, backend=deps.backend)
    draft = new_plan_draft(now_iso=deps.now_iso)
    if hydrated_sections is not None:
        draft["sections"] = hydrated_sections
    return draft


def _save_updated_plan_draft(
    artifact_dir: Path,
    draft: dict[str, object],
    *,
    deps: ArtifactHandlerDeps,
) -> None:
    save_plan_draft(
        artifact_dir,
        draft,
        backend=deps.backend,
        now_iso=deps.now_iso,
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
    draft = _load_or_create_plan_draft(artifact_dir, deps=resolved_deps)
    current_sections = cast("dict[str, object]", draft.get("sections", {}))
    updated_sections = merge_plan_section(current_sections, section, fragment, mode)
    draft["sections"] = updated_sections
    _save_updated_plan_draft(artifact_dir, draft, deps=resolved_deps)

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
    # === BEGIN CANONICAL SUBMIT OPS ===
    from ralph.mcp.artifacts.canonical_submit import (  # noqa: PLC0415
        submit_artifact_canonical,
    )

    submit_artifact_canonical(
        workspace_root=workspace_root,
        artifact_type=PLAN_ARTIFACT_TYPE,
        parsed_content=normalized,
        deps=effective_deps,
        run_id=_session_run_id(session),
        artifact_dir=artifact_dir,
    )
    # === END CANONICAL SUBMIT OPS ===

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


def handle_validate_plan_draft(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Run the full PlanArtifact cross-section validator on the staged draft.

    Read-only: does NOT write ``.agent/artifacts/plan.json`` and does NOT
    delete the in-progress draft. Returns ``{valid: True}`` on success
    or ``{valid: False, errors: [...]}`` on failure. The same checks
    run at ``finalize_plan`` in the write path; this tool exposes them
    in a read-only path so the agent can dry-run validation before
    committing.
    """
    require_capability(session, PLAN_DRAFT_READ_CAPABILITY, "Plan draft validation")
    del params
    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS

    artifact_dir = _resolve_artifact_dir(session, workspace)
    draft = load_plan_draft(artifact_dir, backend=resolved_deps.backend)
    if draft is None:
        return ToolResult(
            content=[
                ToolContent.json_content({"valid": True, "errors": [], "staged_sections": []})
            ],
            is_error=False,
        )

    try:
        finalize_plan_draft(draft)
    except PlanArtifactValidationError as exc:
        return ToolResult(
            content=[
                ToolContent.json_content(
                    {
                        "valid": False,
                        "errors": [{"message": str(exc), "type": type(exc).__name__}],
                    }
                )
            ],
            is_error=False,
        )

    sections_obj = cast("dict[str, object]", draft.get("sections", {}))
    return ToolResult(
        content=[
            ToolContent.json_content(
                {
                    "valid": True,
                    "errors": [],
                    "staged_sections": sorted(sections_obj.keys()),
                }
            )
        ],
        is_error=False,
    )


def _parse_submit_plan_section_entry(
    index: int,
    raw_entry: object,
) -> tuple[str, object, SectionMode] | ToolResult:
    """Parse one batched-section entry; return a ToolResult on any failure.

    Returns a 3-tuple of ``(section, parsed_content, mode)`` on success
    or a ``ToolResult`` (with ``is_error=True``) carrying the
    ``failed_at`` index and the failure message.
    """
    if not isinstance(raw_entry, dict):
        return _submit_sections_error_result(index, f"Entry {index} must be a JSON object")
    entry_dict = cast("dict[str, object]", raw_entry)
    section = entry_dict.get("section")
    content_obj = entry_dict.get("content")
    mode = entry_dict.get("mode", "replace")
    shape_error = _check_entry_shape(index, section, content_obj, mode)
    if shape_error is not None:
        return shape_error
    section_str = cast("str", section)
    mode_str = cast("SectionMode", mode)
    if section_str not in PLAN_SECTION_NAMES:
        return _submit_sections_error_result(
            index,
            f"Unknown plan section '{section_str}'. Valid sections: {sorted(PLAN_SECTION_NAMES)}",
        )
    parsed_content = _decode_entry_content(index, content_obj)
    if isinstance(parsed_content, ToolResult):
        return parsed_content
    type_error = _check_parsed_content_type(index, section_str, mode_str, parsed_content)
    if type_error is not None:
        return type_error
    return section_str, parsed_content, mode_str


def _check_entry_shape(
    index: int,
    section: object,
    content_obj: object,
    mode: object,
) -> ToolResult | None:
    """Return a ToolResult on the first shape failure, or None on success.

    Factored out of ``_parse_submit_plan_section_entry`` to keep the
    ruff PLR0911 cap (≤6 return statements) for the parent function.
    """
    if not isinstance(section, str):
        return _submit_sections_error_result(index, f"Entry {index} missing 'section' string")
    if not isinstance(content_obj, (str, dict, list)):
        return _submit_sections_error_result(
            index, f"Entry {index} 'content' must be a JSON string or object"
        )
    if not isinstance(mode, str) or mode not in ("replace", "append"):
        return _submit_sections_error_result(
            index, f"Entry {index} 'mode' must be 'replace' or 'append'"
        )
    return None


def _decode_entry_content(index: int, content_obj: object) -> object | ToolResult:
    """Decode a JSON-string content payload; pass through dict/list payloads.

    Returns the parsed object on success or a ``ToolResult`` on JSON failure.
    Factored out of ``_parse_submit_plan_section_entry`` to keep its
    return-statement count under the ruff PLR0911 cap.
    """
    if not isinstance(content_obj, str):
        return content_obj
    try:
        decoded: object = json.loads(content_obj)
    except json.JSONDecodeError as exc:
        return _submit_sections_error_result(
            index, f"Entry {index} content must be valid JSON: {exc}"
        )
    return decoded


def _check_parsed_content_type(
    index: int,
    section: str,
    mode: str,
    parsed_content: object,
) -> ToolResult | None:
    """Return a ToolResult on the first type-shape failure, or None on success.

    Factored out of ``_parse_submit_plan_section_entry`` to keep its
    return-statement count under the ruff PLR0911 cap.
    """
    if section in PLAN_SECTION_OBJECT_MODELS and not isinstance(parsed_content, dict):
        return _submit_sections_error_result(
            index, f"Entry {index} (section '{section}') must be a JSON object"
        )
    if (
        section in PLAN_SECTION_LIST_ITEM_MODELS
        and mode == "replace"
        and not isinstance(parsed_content, list)
    ):
        return _submit_sections_error_result(
            index,
            f"Entry {index} (section '{section}') with mode='replace' must be a JSON array",
        )
    if (
        section in PLAN_SECTION_LIST_ITEM_MODELS
        and mode == "append"
        and not isinstance(parsed_content, list)
    ):
        return _submit_sections_error_result(
            index,
            f"Entry {index} (section '{section}') with mode='append' must be a JSON array of items",
        )
    return None


def _submit_sections_error_result(index: int, message: str) -> ToolResult:
    return ToolResult(
        content=[ToolContent.json_content({"submitted": [], "failed_at": index, "error": message})],
        is_error=True,
    )


def _validate_submit_plan_sections_batch(
    parsed_entries: list[tuple[str, object, SectionMode]],
) -> ToolResult | None:
    """Validate the parsed entries; return a ToolResult error on the first failure.

    Returns ``None`` when every entry validates cleanly. The split into
    a separate helper keeps ``handle_submit_plan_sections`` under the
    ruff PLR0911 / PLR0912 caps.
    """
    for index, (section, parsed_content, mode) in enumerate(parsed_entries):
        try:
            if mode == "append" and section in PLAN_SECTION_LIST_ITEM_MODELS:
                items = cast("list[object]", parsed_content)
                for item in items:
                    validate_plan_section(section, item, mode="append")
            else:
                validate_plan_section(section, parsed_content, mode=mode)
        except PlanArtifactValidationError as exc:
            return _submit_sections_error_result(index, f"[{section}] {exc}")
    return None


def _merge_submit_plan_sections_batch(
    parsed_entries: list[tuple[str, object, SectionMode]],
    current_sections: dict[str, object],
) -> tuple[dict[str, object], list[str]] | ToolResult:
    """Apply the batched-section merges onto ``current_sections``.

    Returns the new sections dict + the list of submitted section names
    on success, or a ToolResult error on the first merge failure.
    """
    submitted: list[str] = []
    new_sections: dict[str, object] = current_sections
    for section, parsed_content, mode in parsed_entries:
        try:
            if mode == "append" and section in PLAN_SECTION_LIST_ITEM_MODELS:
                items = cast("list[object]", parsed_content)
                existing_obj = new_sections.get(section)
                existing_list = (
                    list(cast("list[object]", existing_obj))
                    if isinstance(existing_obj, list)
                    else []
                )
                for item in items:
                    fragment = validate_plan_section(section, item, mode="append")
                    existing_list.append(fragment)
                new_sections = merge_plan_section(new_sections, section, existing_list, "replace")
            else:
                fragment = validate_plan_section(section, parsed_content, mode=mode)
                new_sections = merge_plan_section(new_sections, section, fragment, mode)
        except PlanArtifactValidationError as exc:
            return _submit_sections_error_result(len(submitted), f"[{section}] {exc}")
        submitted.append(section)
    return new_sections, submitted


def handle_submit_plan_sections(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Stage a batch of plan sections in a single round-trip.

    Validates EVERY entry before any merge; if any entry fails, the
    entire batch is rejected and the on-disk draft is unchanged. On
    success every entry is merged and the draft is saved once.
    """
    require_capability(session, PLAN_DRAFT_WRITE_CAPABILITY, "Plan sections batched submit")
    entries_obj = params.get("entries")
    if not isinstance(entries_obj, list):
        raise InvalidParamsError("Missing 'entries' array")
    entries = cast("list[object]", entries_obj)

    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS

    parsed_entries: list[tuple[str, object, SectionMode]] = []
    for index, raw_entry in enumerate(entries):
        parsed = _parse_submit_plan_section_entry(index, raw_entry)
        if isinstance(parsed, ToolResult):
            return parsed
        parsed_entries.append(parsed)

    validation_error = _validate_submit_plan_sections_batch(parsed_entries)
    if validation_error is not None:
        return validation_error

    artifact_dir = _resolve_artifact_dir(session, workspace)
    draft = _load_or_create_plan_draft(artifact_dir, deps=resolved_deps)
    current_sections = cast("dict[str, object]", draft.get("sections", {}))
    merge_result = _merge_submit_plan_sections_batch(parsed_entries, current_sections)
    if isinstance(merge_result, ToolResult):
        return merge_result
    new_sections, submitted = merge_result
    draft["sections"] = new_sections
    _save_updated_plan_draft(artifact_dir, draft, deps=resolved_deps)
    serialized = json.dumps(draft, default=str)
    return ToolResult(
        content=[
            ToolContent.json_content(
                {
                    "submitted": submitted,
                    "staged_sections": sorted(new_sections.keys()),
                    "total_bytes": len(serialized),
                }
            )
        ],
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
    try:
        parsed = _parse_content_any(raw_content)
    except InvalidParamsError:
        raise
    if not isinstance(parsed, dict):
        raise InvalidParamsError("Artifact content must decode to a JSON object")
    return cast("dict[str, object]", parsed)


def _parse_content_any(raw_content: str) -> object:
    try:
        return cast("object", json.loads(raw_content))
    except json.JSONDecodeError as exc:
        raise InvalidParamsError(f"Content must be valid JSON: {exc}") from exc


def _parse_plan_content(raw_content: str) -> dict[str, object]:
    """Strict envelope-aware plan payload decoder.

    Delegates to ``parse_plan_payload_strict`` so the four previously
    duplicated JSON parsers share a single source of truth. The strict
    helper raises ``PlanArtifactValidationError`` on invalid JSON or a
    malformed envelope, which we translate to ``InvalidParamsError`` so
    the MCP tool error path stays consistent.
    """
    try:
        return parse_plan_payload_strict(raw_content)
    except PlanArtifactValidationError as exc:
        raise InvalidParamsError(str(exc)) from exc


def _decode_artifact_payload(artifact_type: str, raw_content: str) -> dict[str, object]:
    """Decode the artifact submission content with the type-appropriate parser.

    Plan artifacts use ``parse_plan_payload_strict`` so the four previously
    duplicated JSON parsers share a single envelope-aware core. Every other
    artifact type keeps the dict-required contract.
    """
    if artifact_type == PLAN_ARTIFACT_TYPE:
        decoded = _parse_plan_content(raw_content)
    else:
        decoded = _parse_content(raw_content)
    return _unwrap_persisted_artifact_payload(artifact_type, decoded)


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
        parsed_content = _decode_artifact_payload(artifact_type, raw_content)
    except InvalidParamsError as exc:
        if base_path is not None and has_format_doc(artifact_type):
            _raise_format_doc_error(artifact_type, base_path, backend, exc)
        raise

    try:
        return artifact_type, _normalize_artifact_payload(
            artifact_type, parsed_content, workspace_root=base_path, backend=backend
        )
    except InvalidParamsError as exc:
        if base_path is not None and has_format_doc(artifact_type):
            _raise_format_doc_error(artifact_type, base_path, backend, exc)
        raise


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
        if base_path is not None and has_format_doc(artifact_type):
            _raise_format_doc_error(artifact_type, base_path, backend, exc)
        raise exc

    if not isinstance(raw_content, str):
        exc = InvalidParamsError("Missing 'content' parameter")
        if base_path is not None and has_format_doc(artifact_type):
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


def _session_run_id(session: CoordinationSessionLike) -> str | None:
    run_id = cast("object", getattr(session, "run_id", None))
    return run_id if isinstance(run_id, str) and run_id else None


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
        return _normalize_plan_payload(
            parsed_content, workspace_root=workspace_root, backend=backend
        )
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


def _normalize_plan_payload(
    parsed_content: dict[str, object],
    *,
    workspace_root: Path | None = None,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> dict[str, object]:
    try:
        return normalize_plan_artifact_content(parsed_content)
    except PlanArtifactValidationError as exc:
        if workspace_root is not None and has_format_doc(PLAN_ARTIFACT_TYPE):
            _raise_format_doc_error(PLAN_ARTIFACT_TYPE, workspace_root, backend, exc)
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


# === BEGIN CANONICAL SUBMIT OPS ===
def _submit_ops_for_artifact_with_options(
    artifact_type: str,
    workspace_root: Path,
    artifact_dir: Path,
    parsed_content: dict[str, object],
    *,
    deps: ArtifactHandlerDeps,
    run_id: str | None = None,
    name: str | None = None,
    overwrite: bool = True,
    metadata: dict[str, object] | None = None,
) -> list[SubmitOp]:
    """Return the ordered (op, undo) pairs for a complete artifact submit.

    This private helper is the implementation shared by the public
    :func:`submit_ops_for_artifact` and the canonical submission path. It
    allows bridge callers to supply a custom ``name``, ``overwrite`` policy, and
    ``metadata`` without widening the public op-builder API.
    """
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

    _options = ArtifactSubmitOptions(
        overwrite=overwrite,
        persistence=deps.artifact_persistence,
        metadata=metadata,
    )
    _at = artifact_type
    _name = name or _at
    _content2 = parsed_content
    ops.append(
        SubmitOp(
            run=lambda: submit_artifact(
                artifact_dir,
                name=_name,
                artifact_type=_at,
                content=_content2,
                options=_options,
            ),
            undo=lambda: delete_artifact(artifact_dir, _name, backend=deps.backend),
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

    if run_id is not None:
        _rid = run_id
        _at_receipt = artifact_type
        _wr_receipt = workspace_root
        ops.append(
            SubmitOp(
                run=lambda: write_artifact_receipt(
                    _wr_receipt, _rid, _at_receipt, backend=deps.backend
                ),
                undo=lambda: delete_artifact_receipt(
                    _wr_receipt, _rid, _at_receipt, backend=deps.backend
                ),
            )
        )

    # Architectural fix (2026-06-14): for SINGLE-SHOT artifact types
    # (commit_message, development_result, commit_cleanup, issues, etc.),
    # receipt + completion-sentinel are atomically the same event — the
    # agent has nothing left to do. Mark completion implicitly so a model
    # that interprets "Artifact submitted: <type>" as "done" and stops
    # without calling ``declare_complete`` does not get force-retried in
    # a loop. Multi-step planning artifacts (``plan``,
    # ``*_analysis_decision``) are EXCLUDED: their completion is the
    # explicit ``finalize_plan`` / ``declare_complete`` call.
    if (
        run_id is not None
        and artifact_type != PLAN_ARTIFACT_TYPE
        and artifact_type not in _PLANNING_DECISION_ARTIFACT_TYPES
    ):
        _rid_sentinel = run_id
        _wr_sentinel = workspace_root
        _sentinel_relpath = COMPLETION_SENTINEL_RELPATHFMT.format(run_id=_rid_sentinel)
        _backend = deps.backend
        _sentinel_dict: dict[str, str] = {"run_id": _rid_sentinel}
        _sentinel_payload = json.dumps(_sentinel_dict, ensure_ascii=False)

        def _run_write_sentinel() -> None:
            sentinel_path = _wr_sentinel / _sentinel_relpath
            _backend.mkdir(sentinel_path.parent, parents=True, exist_ok=True)
            _backend.write_text(sentinel_path, _sentinel_payload, encoding="utf-8")

        def _undo_write_sentinel() -> None:
            sentinel_path = _wr_sentinel / _sentinel_relpath
            with suppress(OSError):
                _backend.unlink(sentinel_path, missing_ok=True)

        ops.append(SubmitOp(run=_run_write_sentinel, undo=_undo_write_sentinel))

    return ops


def submit_ops_for_artifact(
    artifact_type: str,
    workspace_root: Path,
    artifact_dir: Path,
    parsed_content: dict[str, object],
    *,
    deps: ArtifactHandlerDeps,
    run_id: str | None = None,
) -> list[SubmitOp]:
    """Return the ordered (op, undo) pairs for a complete artifact submit.

    When ``run_id`` is provided, the final op stamps a run-scoped completion
    receipt for ``artifact_type``. Because it is the last op in the
    rollback-protected sequence, the receipt exists only when the artifact and
    its handoff were fully persisted — binding "submitted" and "gate-visible"
    into one atomic fact regardless of where the artifact bytes landed.
    """
    return _submit_ops_for_artifact_with_options(
        artifact_type,
        workspace_root,
        artifact_dir,
        parsed_content,
        deps=deps,
        run_id=run_id,
        name=artifact_type,
        overwrite=True,
        metadata=None,
    )


# === END CANONICAL SUBMIT OPS ===


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
    "handle_submit_plan_sections",
    "handle_validate_plan_draft",
    "prepare_artifact_submission",
    "submit_ops_for_artifact",
]
