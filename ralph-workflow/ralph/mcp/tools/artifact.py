"""MCP artifact submission handlers.

The artifact surface is the single canonical entry point for agent
artifacts (plan, development_result, review, fix_result,
commit_message, smoke_test_result, typed artifacts). Every public
handler routes through ``ralph.mcp.artifacts.canonical_submit`` and
the supporting persistence / history modules, so the
``audit_artifact_submission_canonical_path`` audit can prove the
single-writer contract.

Exported surface:

- ``handle_submit_artifact`` — the public handler for fully-formed
  artifacts. Resolves the artifact directory (per-worker for
  parallel workers, shared ``.agent/artifacts/`` otherwise), loads
  the policy bundle to compute the ``history_enabled`` flag, and
  delegates to ``submit_artifact_canonical``. Capability:
  ``artifact.submit``.
- ``handle_submit_plan_section`` / ``handle_submit_plan_sections`` —
  public handlers for the incremental plan draft surface. They mutate
  the staged plan draft, validate the section shape against the
  per-section Pydantic model, and merge entries in a single batch
  path. Capability: ``artifact.plan_write``.
- ``handle_finalize_plan`` — the public handler that locks a
  finalized plan, writes the history snapshot, and emits the
  canonical artifact file. Capability: ``artifact.plan_write``.
- ``handle_get_plan_draft`` / ``handle_validate_plan_draft`` /
  ``handle_discard_plan_draft`` — public handlers for the
  resume-after-restart surface. ``get`` returns the current draft
  (or the finalized response when the draft is older than the
  finalized record). ``validate`` is dry-run: it returns the
  per-section validation result without writing. ``discard`` removes
  the staged draft. Capability: ``plan_draft.read`` / ``plan_draft.read``.
- ``ArtifactHandlerDeps`` — the dependency-injection bundle (custom
  ``FileBackend``, ``now_iso`` callable, ``history_enabled`` flag)
  threaded through every handler. ``DEFAULT_ARTIFACT_HANDLER_DEPS``
  is the production default.
- ``_resolve_artifact_dir`` / ``_resolve_history_enabled`` —
  helpers that resolve the per-session / per-drain artifact path and
  the policy-declared history flag.

Trust boundary: every public handler is gated on a ``McpCapability``
declared by the agent session. The plan-draft write capability
(``artifact.plan_write``) is intentionally narrower than the broader
``artifact.submit`` capability so that only the planning drain can
stage or finalize plans; the broad capability is required for the
canonical-submit path used by every other artifact type.

Side effects: writes the canonical artifact file under the resolved
``artifact_dir`` (per-worker or shared), writes the completion receipt,
updates the artifact history index when ``history_enabled`` is true,
and (for the plan-draft handlers) mutates the staged plan draft file.
The submit / finalize / draft-discard paths are all mediated by
``submit_artifact_canonical`` / ``delete_plan_draft`` /
``finalize_plan_draft`` so the audit can prove the single-writer
contract. No subprocess is spawned, no network call is made.
"""

from __future__ import annotations

import ast
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
from ralph.mcp.tools.json_repair import JSON_CONTAINER_FIELD_NAMES, JSON_LIST_FIELD_NAMES
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.policy.models import PipelinePolicy

PLAN_DRAFT_READ_CAPABILITY = Capability.ARTIFACT_PLAN_READ.value
PLAN_DRAFT_WRITE_CAPABILITY = Capability.ARTIFACT_PLAN_WRITE.value
_MIN_CODE_FENCE_LINE_COUNT = 2
_MAX_JSON_REPAIR_CANDIDATES = 64
_JSON_REPAIR_LOOKAHEAD_OFFSET = 1
_BLOCK_COMMENT_WIDTH = 2
_BARE_KEY_MIN_LENGTH = 1
_JSON_LITERAL_REPAIRS = {"null": "None", "true": "True", "false": "False"}


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

_SKILL_POINTERS_BY_ARTIFACT_TYPE: dict[str, str] = {
    COMMIT_MESSAGE_TYPE: "submit-commit-message-artifact",
    DEVELOPMENT_RESULT_ARTIFACT_TYPE: "submit-development-result-artifact",
    "commit_cleanup": "submit-commit-cleanup-artifact",
}


def _per_type_skill_pointer_sentence(artifact_type: str) -> str:
    """Return the per-type skill pointer sentence for ``artifact_type``.

    Returns an empty string when ``artifact_type`` is not in
    ``_SKILL_POINTERS_BY_ARTIFACT_TYPE`` so un-targeted artifact types emit
    only the generic ``submit-artifact`` sentence.
    """
    skill_name = _SKILL_POINTERS_BY_ARTIFACT_TYPE.get(artifact_type)
    if not skill_name:
        return ""
    return (
        f" Optional: the bundled `{skill_name}` skill is the type-specific "
        f"fast-path for `{artifact_type}`."
    )

_KNOWN_ARTIFACT_TYPES = frozenset(
    {PLAN_ARTIFACT_TYPE, COMMIT_MESSAGE_TYPE, DEVELOPMENT_RESULT_ARTIFACT_TYPE}
    | _TYPED_ARTIFACT_TYPES
    | {"review", "verification"}
)

# Planning and analysis-decision artifact types are excluded from the
# auto-complete-on-submit path. Their canonical receipt still counts as
# completion evidence for the current run, but the submit handler itself
# must not unconditionally stamp the single-shot completion sentinel.
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
    """Validate and persist an artifact submitted by an MCP agent.

    The handler is the public MCP entry point for any fully-formed
    artifact (development_result, review / issues, fix_result,
    commit_message, smoke_test_result, typed artifacts). It resolves the
    artifact directory (per-worker for parallel workers, the shared
    ``.agent/artifacts/`` otherwise), loads the policy bundle to compute
    the ``history_enabled`` flag, and delegates the persistence side
    effects to ``submit_artifact_canonical`` so the
    ``audit_artifact_submission_canonical_path`` audit can prove the
    single-writer contract.

    Args:
        session: Agent session carrying the capability set, run id, and
            optional ``worker_artifact_dir`` override for parallel
            workers.
        workspace: Workspace surface that resolves the artifact root.
        params: ``artifact_type`` (string) and ``content`` (object or
            raw JSON text) per the artifact contract. The handler also
            reads the optional ``draft`` / ``format`` keys used by the
            canonical submission path.
        deps: Optional dependency-injection bundle (custom
            ``FileBackend``, ``now_iso`` callable, ``history_enabled``
            override). When ``None``, ``DEFAULT_ARTIFACT_HANDLER_DEPS``
            is used.

    Returns:
        A success ``ToolResult`` (text: ``Artifact submitted: <type>``).
        The canonical artifact file, completion receipt, and optional
        history snapshot are written as side effects.

    Raises:
        CapabilityDeniedError: When the session does not declare
            ``artifact.submit``. The handler enforces default-deny.
        Pydantic ``ValidationError`` (wrapped in the artifact's typed
            ``ValidationError`` subclass — for example,
            ``PlanArtifactValidationError``)
            when the payload fails the per-type schema check. The
            canonical submit path converts these into actionable
            per-field error messages.

    Side effects:
        Resolves the artifact directory (per-worker for parallel
        workers, the shared ``.agent/artifacts/`` otherwise) and
        delegates to ``submit_artifact_canonical``, which writes the
        canonical artifact file, the completion receipt, and (when the
        active policy declares ``artifact_history.enabled = true``)
        the history snapshot under the resolved ``artifact_dir``. No
        subprocess is spawned, no network call is made.
    """
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
    """Stage a single plan section and report validation warnings.

    The handler is the public MCP entry point for the incremental
    plan-draft surface. It accepts a single ``section`` name (one of
    ``PLAN_SECTION_NAMES``), a ``mode`` (``replace`` | ``append`` |
    ``merge``), and a JSON-serializable ``payload``, validates the
    payload against the per-section Pydantic model, and persists the
    merged draft under the resolved ``artifact_dir``.

    Args:
        session: Agent session carrying the capability set, run id, and
            optional ``worker_artifact_dir`` override for parallel
            workers.
        workspace: Workspace surface that resolves the artifact root.
        params: ``section`` (string), ``mode`` (string, default
            ``"replace"``), and ``payload`` (JSON-serializable object or
            raw JSON text).
        deps: Optional dependency-injection bundle. When ``None``,
            ``DEFAULT_ARTIFACT_HANDLER_DEPS`` is used.

    Returns:
        A ``ToolResult`` with the staged-section text and any
        non-fatal validation warnings surfaced inline.

    Raises:
        CapabilityDeniedError: When the session does not declare
            ``plan_draft.write``.
        InvalidParamsError: When ``section`` is missing, unknown, or the
            payload fails the per-section schema check. The handler
            formats the error so the agent sees a clear ``[section]``
            marker and the underlying ``PlanArtifactValidationError``
            detail.

    Side effects:
        Resolves the artifact directory (per-worker for parallel
        workers, the shared ``.agent/artifacts/`` otherwise) and
        persists the merged draft under the resolved ``artifact_dir``
        via ``save_plan_draft``. Schema-invalid but shape-valid sections
        are staged with ``validation_warnings`` and do NOT block
        persistence — the strict gates are ``ralph_validate_draft`` and
        ``ralph_finalize_plan``. No subprocess is spawned, no network
        call is made.
    """
    require_capability(session, PLAN_DRAFT_WRITE_CAPABILITY, "Plan section submission")

    try:
        section = _required_string(params, "section")
    except InvalidParamsError as exc:
        workspace_root = _workspace_root(workspace)
        resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS
        raise InvalidParamsError(
            _format_plan_section_submission_error(
                section="<missing>",
                mode="replace",
                detail=str(exc),
                workspace_root=workspace_root,
                backend=resolved_deps.backend,
                tool_name="ralph_submit_plan_section",
            )
        ) from exc
    if section not in PLAN_SECTION_NAMES:
        workspace_root = _workspace_root(workspace)
        resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS
        raise InvalidParamsError(
            _format_plan_section_submission_error(
                section=section,
                mode="replace",
                detail=(
                    f"Unknown plan section '{section}'. Valid sections: "
                    f"{sorted(PLAN_SECTION_NAMES)}"
                ),
                workspace_root=workspace_root,
                backend=resolved_deps.backend,
                tool_name="ralph_submit_plan_section",
            )
        )
    try:
        mode = _section_mode(params)
    except InvalidParamsError as exc:
        workspace_root = _workspace_root(workspace)
        resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS
        raise InvalidParamsError(
            _format_plan_section_submission_error(
                section=section,
                mode="<invalid>",
                detail=str(exc),
                workspace_root=workspace_root,
                backend=resolved_deps.backend,
                tool_name="ralph_submit_plan_section",
            )
        ) from exc

    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS
    workspace_root = _workspace_root(workspace)
    if "content" not in params:
        raise InvalidParamsError(
            _format_plan_section_submission_error(
                section=section,
                mode=mode,
                detail="Missing 'content' (must be valid JSON)",
                workspace_root=workspace_root,
                backend=resolved_deps.backend,
                tool_name="ralph_submit_plan_section",
            )
        )
    raw_content = params.get("content")
    payload = _parse_plan_section_content(
        raw_content,
        section=section,
        mode=mode,
        workspace_root=workspace_root,
        backend=resolved_deps.backend,
    )

    artifact_dir = _resolve_artifact_dir(session, workspace)
    draft = _load_or_create_plan_draft(artifact_dir, deps=resolved_deps)
    current_sections = cast("dict[str, object]", draft.get("sections", {}))

    try:
        updated_sections, merge_mode, validation_warnings = _stage_plan_section_fragment(
            current_sections,
            section,
            payload,
            mode,
        )
    except PlanArtifactValidationError as exc:
        raise InvalidParamsError(
            _format_plan_section_submission_error(
                section=section,
                mode=mode,
                detail=f"[{section}] {exc}",
                workspace_root=workspace_root,
                backend=resolved_deps.backend,
                tool_name="ralph_submit_plan_section",
            )
        ) from exc

    draft["sections"] = updated_sections
    _save_updated_plan_draft(artifact_dir, draft, deps=resolved_deps)

    staged = sorted(updated_sections.keys())
    return ToolResult(
        content=[
            ToolContent.json_content(
                {
                    "submitted": [section],
                    "section": section,
                    "mode": merge_mode,
                    "staged_sections": staged,
                    "validation_warnings": validation_warnings,
                }
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
    """Validate the staged draft as a whole plan and write plan.json.

    The handler validates every staged section of the plan draft as a
    single artifact, writes the canonical ``plan.json`` under the
    resolved ``artifact_dir``, takes a history snapshot when
    ``history_enabled`` is true, and returns the canonical finalize
    response that downstream phases consume.

    Args:
        session: Agent session carrying the capability set, run id, and
            optional ``worker_artifact_dir`` override for parallel
            workers.
        workspace: Workspace surface that resolves the artifact root.
        params: Optional overrides accepted by the canonical finalize
            path (e.g. ``timestamp``). Unknown keys are ignored.
        deps: Optional dependency-injection bundle. When ``None``,
            ``DEFAULT_ARTIFACT_HANDLER_DEPS`` is used.

    Returns:
        A ``ToolResult`` with the canonical finalize response JSON,
        including the validated plan content, the final ``updated_at``
        timestamp, and the ``source`` discriminator (``"finalized"``).

    Raises:
        CapabilityDeniedError: When the session does not declare
            ``artifact.plan_write``.
        PlanArtifactValidationError: When the assembled draft fails the
            whole-plan schema check (e.g. missing required sections,
            step number collision, cross-section reference violation).

    Side effects:
        Resolves the artifact directory (per-worker for parallel
        workers, the shared ``.agent/artifacts/`` otherwise) and
        delegates to ``submit_artifact_canonical``, which writes the
        canonical ``plan.json`` artifact file, the completion receipt,
        and (when the active policy declares
        ``artifact_history.enabled = true``) the history snapshot under
        the resolved ``artifact_dir``. The structured JSON artifact is
        mirrored into a Markdown handoff via ``sync_markdown_handoff``
        so downstream phases never need to read raw JSON directly. No
        subprocess is spawned, no network call is made.
    """
    require_capability(session, PLAN_DRAFT_WRITE_CAPABILITY, "Plan finalization")
    del params
    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS
    workspace_root = _workspace_root(workspace)

    artifact_dir = _resolve_artifact_dir(session, workspace)
    draft = load_plan_draft(artifact_dir, backend=resolved_deps.backend)
    if draft is None:
        raise InvalidParamsError(
            _format_plan_finalize_error(
                detail=(
                    "No plan draft to finalize. Submit plan sections first with "
                    "ralph_submit_plan_section or ralph_submit_plan_sections."
                ),
                workspace_root=workspace_root,
                backend=resolved_deps.backend,
                tool_name="ralph_finalize_plan",
            )
        )

    try:
        normalized = finalize_plan_draft(draft)
    except PlanArtifactValidationError as exc:
        raise InvalidParamsError(
            _format_plan_finalize_error(
                detail=str(exc),
                workspace_root=workspace_root,
                backend=resolved_deps.backend,
                tool_name="ralph_finalize_plan",
            )
        ) from exc

    # Keep the structured JSON artifact for Ralph's validation/routing, but
    # always mirror agent/user-consumed artifacts into Markdown handoffs so
    # downstream phases never need to read raw JSON directly.
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
    """Return the current plan draft so an agent can resume after a restart.

    The handler is the resume-after-restart entry point. It returns the
    current plan draft (or the finalized response when the draft is
    older than the finalized record) so an agent that resumes after a
    crash or restart can pick up where it left off. The ``params``
    argument is reserved for future filters and is currently ignored.

    Args:
        session: Agent session carrying the capability set, run id, and
            optional ``worker_artifact_dir`` override for parallel
            workers.
        workspace: Workspace surface that resolves the artifact root.
        params: Reserved for future filters. Currently ignored.
        deps: Optional dependency-injection bundle. When ``None``,
            ``DEFAULT_ARTIFACT_HANDLER_DEPS`` is used.

    Returns:
        A ``ToolResult`` with the plan-draft response JSON
        (``staged_sections`` list, ``started_at`` / ``updated_at``
        timestamps, ``draft`` sections dict, and ``source``
        discriminator — ``"draft"`` or ``"finalized"``).

    Raises:
        CapabilityDeniedError: When the session does not declare
            ``plan_draft.read``.

    Side effects:
        None. Read-only: resolves the artifact directory (per-worker or
        shared), loads the staged draft via ``load_plan_draft`` and the
        finalized response via ``load_plan_artifact_sections``, and
        returns whichever is newer. Does NOT write
        ``.agent/artifacts/plan.json`` and does NOT delete the
        in-progress draft. No subprocess is spawned, no network call is
        made.
    """
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
    delete the in-progress draft. The same checks run at
    ``finalize_plan`` in the write path; this tool exposes them in a
    read-only path so the agent can dry-run validation before committing.

    Args:
        session: Agent session carrying the capability set, run id, and
            optional ``worker_artifact_dir`` override for parallel
            workers.
        workspace: Workspace surface that resolves the artifact root.
        params: Reserved for future filters. Currently ignored.
        deps: Optional dependency-injection bundle. When ``None``,
            ``DEFAULT_ARTIFACT_HANDLER_DEPS`` is used.

    Returns:
        A ``ToolResult`` with the canonical validation response JSON.
        On success: ``{"valid": true, "errors": [], "staged_sections":
        [...]}`` with ``is_error=False``. On a missing draft:
        ``{"valid": false, "errors": [{"message": ..., "type":
        "InvalidDraftState"}], "staged_sections": []}`` with
        ``is_error=False`` (the missing-draft case is reported in-body,
        not as an MCP error). On a schema failure:
        ``{"valid": false, "errors": [{"message": ..., "type":
        "PlanArtifactValidationError"}]}`` with ``is_error=False``.

    Raises:
        CapabilityDeniedError: When the session does not declare
            ``plan_draft.read``.

    Side effects:
        None. Read-only: resolves the artifact directory (per-worker or
        shared) and runs ``finalize_plan_draft(draft)`` for its
        ``PlanArtifactValidationError`` side effect — any per-section
        ``PlanArtifactValidationError`` is converted into the
        ``{"valid": false, "errors": [...]}`` JSON response and the
        in-progress draft is left untouched. Does NOT write
        ``.agent/artifacts/plan.json`` and does NOT delete the draft. No
        subprocess is spawned, no network call is made.
    """
    require_capability(session, PLAN_DRAFT_READ_CAPABILITY, "Plan draft validation")
    del params
    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS

    artifact_dir = _resolve_artifact_dir(session, workspace)
    draft = load_plan_draft(artifact_dir, backend=resolved_deps.backend)
    if draft is None:
        return ToolResult(
            content=[
                ToolContent.json_content(
                    {
                        "valid": False,
                        "errors": [
                            {
                                "message": (
                                    _format_plan_finalize_error(
                                        detail=(
                                            "No plan draft to validate. Submit plan sections first "
                                            "with ralph_submit_plan_section or "
                                            "ralph_submit_plan_sections."
                                        ),
                                        workspace_root=_workspace_root(workspace),
                                        backend=resolved_deps.backend,
                                        tool_name="ralph_validate_draft",
                                    )
                                ),
                                "type": "InvalidDraftState",
                            }
                        ],
                        "staged_sections": [],
                    }
                )
            ],
            is_error=False,
        )

    try:
        finalize_plan_draft(draft)
    except PlanArtifactValidationError as exc:
        workspace_root = _workspace_root(workspace)
        return ToolResult(
            content=[
                ToolContent.json_content(
                    {
                        "valid": False,
                        "errors": [
                            {
                                "message": _format_plan_finalize_error(
                                    detail=str(exc),
                                    workspace_root=workspace_root,
                                    backend=resolved_deps.backend,
                                    tool_name="ralph_validate_draft",
                                ),
                                "type": type(exc).__name__,
                            }
                        ],
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
    *,
    workspace_root: Path,
    backend: FileBackend,
) -> tuple[str, object, SectionMode] | ToolResult:
    """Parse one batched-section entry; return a ToolResult on any failure.

    Returns a 3-tuple of ``(section, parsed_content, mode)`` on success
    or a ``ToolResult`` (with ``is_error=True``) carrying the
    ``failed_at`` index and the failure message.
    """
    if not isinstance(raw_entry, dict):
        return _submit_sections_error_result(
            index,
            _format_plan_batch_envelope_error(
                detail=f"Entry {index} must be a JSON object",
                workspace_root=workspace_root,
                backend=backend,
            ),
        )
    entry_dict = cast("dict[str, object]", raw_entry)
    section = entry_dict.get("section")
    content_obj = entry_dict.get("content")
    has_content = "content" in entry_dict
    mode = entry_dict.get("mode", "replace")
    shape_error = _check_entry_shape(
        index,
        section,
        content_obj,
        mode,
        has_content=has_content,
        workspace_root=workspace_root,
        backend=backend,
    )
    if shape_error is not None:
        return shape_error
    section_str = cast("str", section)
    mode_str = cast("SectionMode", mode)
    if section_str not in PLAN_SECTION_NAMES:
        return _submit_sections_error_result(
            index,
            _format_plan_batch_envelope_error(
                detail=(
                    f"Unknown plan section '{section_str}'. Valid sections: "
                    f"{sorted(PLAN_SECTION_NAMES)}"
                ),
                workspace_root=workspace_root,
                backend=backend,
            ),
        )
    parsed_content = _decode_entry_content(
        index,
        content_obj,
        section=section_str,
        mode=mode_str,
        workspace_root=workspace_root,
        backend=backend,
    )
    if isinstance(parsed_content, ToolResult):
        return parsed_content
    type_error = _check_parsed_content_type(
        index,
        section_str,
        mode_str,
        parsed_content,
        workspace_root=workspace_root,
        backend=backend,
    )
    if type_error is not None:
        return type_error
    return section_str, parsed_content, mode_str


def _check_entry_shape(
    index: int,
    section: object,
    content_obj: object,
    mode: object,
    *,
    has_content: bool,
    workspace_root: Path,
    backend: FileBackend,
) -> ToolResult | None:
    """Return a ToolResult on the first shape failure, or None on success.

    Factored out of ``_parse_submit_plan_section_entry`` to keep the
    ruff PLR0911 cap (≤6 return statements) for the parent function.
    """
    if not isinstance(section, str):
        return _submit_sections_error_result(
            index,
            _format_plan_batch_envelope_error(
                detail=f"Entry {index} missing 'section' string",
                workspace_root=workspace_root,
                backend=backend,
            ),
        )
    if not has_content:
        return _submit_sections_error_result(
            index,
            _format_plan_batch_envelope_error(
                detail=f"Entry {index} missing 'content'",
                workspace_root=workspace_root,
                backend=backend,
            ),
        )
    if not isinstance(mode, str) or mode not in ("replace", "append"):
        return _submit_sections_error_result(
            index,
            _format_plan_batch_envelope_error(
                detail=f"Entry {index} 'mode' must be 'replace' or 'append'",
                workspace_root=workspace_root,
                backend=backend,
            ),
        )
    return None


def _decode_entry_content(
    index: int,
    content_obj: object,
    *,
    section: str,
    mode: str,
    workspace_root: Path,
    backend: FileBackend,
) -> object | ToolResult:
    """Decode a JSON-string content payload; pass through dict/list payloads.

    Returns the parsed object on success or a ``ToolResult`` on JSON failure.
    Factored out of ``_parse_submit_plan_section_entry`` to keep its
    return-statement count under the ruff PLR0911 cap.
    """
    if not isinstance(content_obj, str):
        return _normalize_plan_section_payload(section, content_obj, mode=mode)
    try:
        decoded = _parse_content_any(content_obj)
    except InvalidParamsError as exc:
        return _submit_sections_error_result(
            index,
            _format_plan_section_submission_error(
                section=section,
                mode=mode,
                detail=str(exc),
                workspace_root=workspace_root,
                backend=backend,
                tool_name="ralph_submit_plan_sections",
            ),
        )
    return _normalize_plan_section_payload(section, decoded, mode=mode)


def _check_parsed_content_type(
    index: int,
    section: str,
    mode: str,
    parsed_content: object,
    *,
    workspace_root: Path,
    backend: FileBackend,
) -> ToolResult | None:
    """Keep structural parsing permissive; validate/finalize enforce schema."""
    del index, section, mode, parsed_content, workspace_root, backend
    return None


def _append_items(payload: object) -> list[object]:
    if isinstance(payload, list):
        return payload
    return [payload]


def _submit_sections_error_result(index: int, message: str) -> ToolResult:
    return ToolResult(
        content=[ToolContent.json_content({"submitted": [], "failed_at": index, "error": message})],
        is_error=True,
    )


def _plan_section_shape_error(
    section: str,
    payload: object,
    mode: SectionMode,
) -> PlanArtifactValidationError | None:
    """Return a shape error (NOT a schema error) for wrong container types.

    Shape errors are NOT eligible for lenient staging: a single dict
    passed to a list section in replace mode is a structural mistake,
    not a content-quality issue, so the tool rejects it with
    ``isError=True`` instead of staging it with a warning. Schema
    errors (valid shape, wrong field types/values) are still
    eligible for lenient staging via ``_plan_section_validation_warnings``.
    """
    if (
        (section in PLAN_SECTION_LIST_ITEM_MODELS or section == "work_units")
        and mode == "replace"
        and not isinstance(payload, list)
    ):
        return PlanArtifactValidationError(
            f"section '{section}' with mode='replace' must be a JSON array"
        )
    return None


def _plan_section_validation_warnings(
    section: str,
    payload: object,
    mode: SectionMode,
) -> list[str]:
    shape_error = _plan_section_shape_error(section, payload, mode)
    if shape_error is not None:
        raise shape_error
    try:
        validate_plan_section(section, payload, mode=mode)
    except PlanArtifactValidationError as exc:
        return [
            f"[{section}] staged section does not yet pass schema validation; "
            f"run ralph_validate_draft before finalize: {exc}"
        ]
    return []


def _validated_or_raw_plan_section(
    section: str,
    payload: object,
    mode: SectionMode,
) -> tuple[object, list[str]]:
    warnings = _plan_section_validation_warnings(section, payload, mode)
    if warnings:
        return payload, warnings
    return validate_plan_section(section, payload, mode=mode), []


def _stage_plan_section_fragment(
    current_sections: dict[str, object],
    section: str,
    parsed_content: object,
    mode: SectionMode,
) -> tuple[dict[str, object], SectionMode, list[str]]:
    if mode == "append" and section in PLAN_SECTION_OBJECT_MODELS:
        raise PlanArtifactValidationError(
            f"section '{section}' only supports mode='replace'"
        )
    if mode == "append" and (section in PLAN_SECTION_LIST_ITEM_MODELS or section == "work_units"):
        items = _append_items(parsed_content)
        existing_obj = current_sections.get(section)
        existing_list = (
            list(cast("list[object]", existing_obj)) if isinstance(existing_obj, list) else []
        )
        existing_list.extend(items)
        fragment, warnings = _validated_or_raw_plan_section(
            section,
            existing_list,
            "replace",
        )
        updated_sections = merge_plan_section(
            current_sections,
            section,
            fragment,
            "replace",
        )
        return updated_sections, "replace", warnings
    fragment, warnings = _validated_or_raw_plan_section(section, parsed_content, mode)
    updated_sections = merge_plan_section(current_sections, section, fragment, mode)
    return updated_sections, mode, warnings


def _merge_submit_plan_sections_batch(
    parsed_entries: list[tuple[str, object, SectionMode]],
    current_sections: dict[str, object],
) -> tuple[dict[str, object], list[str], list[str]]:
    """Apply the batched-section merges onto ``current_sections``.

    Returns the new sections dict, submitted section names, and validation
    warnings. Schema warnings do not reject staging; validate/finalize remain
    the strict gates.
    """
    submitted: list[str] = []
    validation_warnings: list[str] = []
    new_sections: dict[str, object] = current_sections
    for section, parsed_content, mode in parsed_entries:
        new_sections, _merge_mode, warnings = _stage_plan_section_fragment(
            new_sections,
            section,
            parsed_content,
            mode,
        )
        validation_warnings.extend(warnings)
        submitted.append(section)
    return new_sections, submitted, validation_warnings


def handle_submit_plan_sections(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Stage a batch of plan sections in a single round-trip.

    Parses EVERY entry before any merge; if any entry is structurally
    malformed, the entire batch is rejected and the on-disk draft is
    unchanged. Schema-invalid but valid JSON sections are staged with
    validation_warnings so validate/finalize can be the strict gates.

    Args:
        session: Agent session carrying the capability set, run id, and
            optional ``worker_artifact_dir`` override for parallel
            workers.
        workspace: Workspace surface that resolves the artifact root.
        params: ``entries`` (list of ``{section, mode, content}``
            dicts; each entry may inline the content or carry it as a
            JSON-encoded string). The handler also accepts a
            single-JSON-string container or a pre-coerced list. Unknown
            top-level keys are ignored.
        deps: Optional dependency-injection bundle. When ``None``,
            ``DEFAULT_ARTIFACT_HANDLER_DEPS`` is used.

    Returns:
        A ``ToolResult`` with the canonical batch response JSON on
        success: ``{"submitted": [...], "staged_sections": [...],
        "total_bytes": <int>, "validation_warnings": [...]}`` with
        ``is_error=False``. On a malformed envelope
        (``entries`` missing or wrong type) or a single structurally
        malformed entry: ``{"submitted": [], "failed_at": <int>,
        "error": <message>}`` with ``is_error=True`` (the entire
        batch is rejected and the on-disk draft is unchanged).

    Raises:
        CapabilityDeniedError: When the session does not declare
            ``plan_draft.write``.
        InvalidParamsError: When ``entries`` is missing, is the wrong
            container type, or cannot be coerced from the supplied JSON
            text. The handler formats the error so the agent sees a
            clear ``entries`` envelope marker.

    Side effects:
        Resolves the artifact directory (per-worker for parallel
        workers, the shared ``.agent/artifacts/`` otherwise) and
        persists the merged draft under the resolved ``artifact_dir``
        via ``save_plan_draft``. Schema-invalid but shape-valid sections
        are staged with ``validation_warnings`` and do NOT block
        persistence — the strict gates are ``ralph_validate_draft`` and
        ``ralph_finalize_plan``. A structurally malformed entry
        short-circuits before any merge, so the on-disk draft is
        unchanged when the batch is rejected. No subprocess is spawned,
        no network call is made.
    """
    require_capability(session, PLAN_DRAFT_WRITE_CAPABILITY, "Plan sections batched submit")
    resolved_deps = deps or DEFAULT_ARTIFACT_HANDLER_DEPS
    workspace_root = _workspace_root(workspace)
    entries_obj = params.get("entries")
    if isinstance(entries_obj, str):
        entries_obj = _coerce_json_text_container(entries_obj)
    else:
        entries_obj = _coerce_json_list_field(entries_obj)
    if not isinstance(entries_obj, list):
        raise InvalidParamsError(
            _format_plan_batch_envelope_error(
                detail="Missing 'entries' array",
                workspace_root=workspace_root,
                backend=resolved_deps.backend,
            )
        )
    entries = cast("list[object]", entries_obj)

    parsed_entries: list[tuple[str, object, SectionMode]] = []
    for index, raw_entry in enumerate(entries):
        parsed = _parse_submit_plan_section_entry(
            index,
            raw_entry,
            workspace_root=workspace_root,
            backend=resolved_deps.backend,
        )
        if isinstance(parsed, ToolResult):
            return parsed
        parsed_entries.append(parsed)

    artifact_dir = _resolve_artifact_dir(session, workspace)
    draft = _load_or_create_plan_draft(artifact_dir, deps=resolved_deps)
    current_sections = cast("dict[str, object]", draft.get("sections", {}))
    try:
        new_sections, submitted, validation_warnings = _merge_submit_plan_sections_batch(
            parsed_entries,
            current_sections,
        )
    except PlanArtifactValidationError as exc:
        return _submit_sections_error_result(
            0,
            _format_plan_batch_envelope_error(
                detail=str(exc),
                workspace_root=workspace_root,
                backend=resolved_deps.backend,
            ),
        )
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
                    "validation_warnings": validation_warnings,
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
    """Delete the on-disk plan draft so the agent can start over.

    Args:
        session: Agent session carrying the capability set, run id, and
            optional ``worker_artifact_dir`` override for parallel
            workers.
        workspace: Workspace surface that resolves the artifact root.
        params: Reserved for future filters. Currently ignored.
        deps: Optional dependency-injection bundle. When ``None``,
            ``DEFAULT_ARTIFACT_HANDLER_DEPS`` is used.

    Returns:
        A ``ToolResult`` with ``"Plan draft discarded."`` when a draft
        existed and was deleted, or ``"No plan draft to discard."``
        when there was no draft on disk.

    Raises:
        CapabilityDeniedError: When the session does not declare
            ``plan_draft.write``.

    Side effects:
        Resolves the artifact directory (per-worker for parallel
        workers, the shared ``.agent/artifacts/`` otherwise) and calls
        ``delete_plan_draft`` to remove the staged plan draft file when
        present. The handler does NOT touch the finalized plan artifact
        (that is the write path's concern) and does NOT mutate the
        history index. No subprocess is spawned, no network call is
        made.
    """
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


def _loads_json_text_one_or_two_layers(raw_content: str) -> object:
    """Decode JSON text, unwrapping one double-encoded layer when valid."""
    parsed = _loads_json_text_one_layer(raw_content)
    if not isinstance(parsed, str):
        return parsed
    try:
        return _loads_json_text_one_layer(parsed)
    except InvalidParamsError:
        return parsed


def _loads_json_text_one_layer(raw_content: str) -> object:
    """Decode JSON text after deterministic, non-ambiguous repairs."""
    first_error: json.JSONDecodeError | None = None
    for candidate in _json_text_candidates(raw_content):
        try:
            return cast("object", json.loads(candidate))
        except json.JSONDecodeError as exc:
            if first_error is None:
                first_error = exc
        literal = _literal_container(candidate)
        if literal is not None:
            return literal
    if first_error is None:
        try:
            json.loads(raw_content)
        except json.JSONDecodeError as exc:
            first_error = exc
    if first_error is None:
        raise InvalidParamsError("Content must be valid JSON")
    raise InvalidParamsError(f"Content must be valid JSON: {first_error}") from first_error


def _json_text_candidates(raw_content: str) -> list[str]:
    """Return parse candidates that each imply one container unambiguously."""
    candidates: list[str] = []

    def add(candidate: str) -> None:
        stripped = candidate.strip()
        if (
            stripped
            and stripped not in candidates
            and len(candidates) < _MAX_JSON_REPAIR_CANDIDATES
        ):
            candidates.append(stripped)

    add(raw_content)
    fenced = _strip_full_code_fence(raw_content)
    if fenced is not None:
        add(fenced)
    extracted = _extract_single_container(raw_content)
    if extracted is not None:
        add(extracted)
    index = 0
    while index < len(candidates):
        candidate = candidates[index]
        for repaired in _json_repair_candidates(candidate):
            add(repaired)
        index += 1
    return candidates


def _json_repair_candidates(candidate: str) -> list[str]:
    """Return deterministic repairs for common model-produced JSON mistakes."""
    return [
        _strip_json_comments_and_trailing_commas(candidate),
        _normalize_smart_quote_delimiters(candidate),
        _quote_bare_identifier_keys(candidate),
        _replace_unquoted_json_literals_for_python(candidate),
        _insert_missing_value_commas(candidate),
    ]


def _literal_container(candidate: str) -> object | None:
    """Parse Python-literal dict/list text only when it is a container."""
    try:
        parsed: object = ast.literal_eval(candidate)
    except (SyntaxError, ValueError):
        return None
    if not isinstance(parsed, (dict, list)):
        return None
    return parsed if _is_json_compatible_literal(parsed) else None


def _is_json_compatible_literal(value: object) -> bool:
    """Return true when a Python literal can be represented as JSON."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(_is_json_compatible_literal(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_compatible_literal(item)
            for key, item in value.items()
        )
    return False


def _strip_full_code_fence(raw_content: str) -> str | None:
    stripped = raw_content.strip()
    if not stripped.startswith("```") or not stripped.endswith("```"):
        return None
    lines = stripped.splitlines()
    if (
        len(lines) < _MIN_CODE_FENCE_LINE_COUNT
        or not lines[0].startswith("```")
        or lines[-1].strip() != "```"
    ):
        return None
    return "\n".join(lines[1:-1]).strip()


def _strip_json_comments_and_trailing_commas(raw_content: str) -> str:
    no_comments = _strip_json_comments(raw_content)
    return _strip_trailing_commas(no_comments)


def _normalize_smart_quote_delimiters(raw_content: str) -> str:
    return raw_content.translate(
        {
            ord("\u201c"): '"',
            ord("\u201d"): '"',
            ord("\u2018"): "'",
            ord("\u2019"): "'",
        }
    )


def _quote_bare_identifier_keys(raw_content: str) -> str:
    output: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(raw_content):
        char = raw_content[index]
        if quote is not None:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in ('"', "'"):
            quote = char
            output.append(char)
            index += 1
            continue
        if _is_ascii_identifier_start(char) and _can_start_bare_key(output):
            key_end = index + _BARE_KEY_MIN_LENGTH
            while key_end < len(raw_content) and _is_ascii_identifier_char(raw_content[key_end]):
                key_end += 1
            next_index = _next_nonspace_index(raw_content, key_end)
            if next_index < len(raw_content) and raw_content[next_index] == ":":
                output.extend(('"', raw_content[index:key_end], '"'))
                index = key_end
                continue
        output.append(char)
        index += 1
    return "".join(output)


def _replace_unquoted_json_literals_for_python(raw_content: str) -> str:
    output: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(raw_content):
        char = raw_content[index]
        if quote is not None:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in ('"', "'"):
            quote = char
            output.append(char)
            index += 1
            continue
        replacement = _json_literal_replacement_at(raw_content, index)
        if replacement is not None:
            source, target = replacement
            output.append(target)
            index += len(source)
            continue
        output.append(char)
        index += 1
    return "".join(output)


def _insert_missing_value_commas(raw_content: str) -> str:
    output: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(raw_content):
        char = raw_content[index]
        if quote is not None:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in ('"', "'"):
            quote = char
            output.append(char)
            index += 1
            continue
        if char.isspace():
            previous = _previous_nonspace_char(output)
            next_index = _next_nonspace_index(
                raw_content,
                index + _JSON_REPAIR_LOOKAHEAD_OFFSET,
            )
            if (
                previous is not None
                and next_index < len(raw_content)
                and _is_json_value_boundary(previous, raw_content[next_index])
            ):
                output.append(",")
        output.append(char)
        index += 1
    return "".join(output)


def _json_literal_replacement_at(raw_content: str, index: int) -> tuple[str, str] | None:
    for source, target in _JSON_LITERAL_REPAIRS.items():
        if (
            raw_content.startswith(source, index)
            and _is_word_boundary(raw_content, index - 1)
            and _is_word_boundary(raw_content, index + len(source))
        ):
            return source, target
    return None


def _can_start_bare_key(output: list[str]) -> bool:
    previous = _previous_nonspace_char(output)
    return previous in ("{", ",")


def _previous_nonspace_char(chars: list[str]) -> str | None:
    for char in reversed(chars):
        if not char.isspace():
            return char
    return None


def _next_nonspace_index(raw_content: str, index: int) -> int:
    while index < len(raw_content) and raw_content[index].isspace():
        index += 1
    return index


def _is_ascii_identifier_start(char: str) -> bool:
    return char == "_" or "A" <= char <= "Z" or "a" <= char <= "z"


def _is_ascii_identifier_char(char: str) -> bool:
    return _is_ascii_identifier_start(char) or "0" <= char <= "9"


def _is_word_boundary(raw_content: str, index: int) -> bool:
    return index < 0 or index >= len(raw_content) or not _is_ascii_identifier_char(
        raw_content[index]
    )


def _is_json_value_boundary(previous: str, next_char: str) -> bool:
    return _can_end_json_value(previous) and _can_start_json_value(next_char)


def _can_end_json_value(char: str) -> bool:
    return char in ('}', ']', '"', "'", "e", "E", "l") or "0" <= char <= "9"


def _can_start_json_value(char: str) -> bool:
    return char in ('{', '[', '"', "'", "-", "t", "f", "n") or "0" <= char <= "9"


def _strip_json_comments(raw_content: str) -> str:
    output: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(raw_content):
        char = raw_content[index]
        next_char = raw_content[index + 1] if index + 1 < len(raw_content) else ""
        if quote is not None:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in ('"', "'"):
            quote = char
            output.append(char)
            index += 1
            continue
        if char == "/" and next_char == "/":
            index += _BLOCK_COMMENT_WIDTH
            while index < len(raw_content) and raw_content[index] not in "\r\n":
                index += 1
            continue
        if char == "/" and next_char == "*":
            index += _BLOCK_COMMENT_WIDTH
            while (
                index + _JSON_REPAIR_LOOKAHEAD_OFFSET < len(raw_content)
                and raw_content[index : index + _BLOCK_COMMENT_WIDTH] != "*/"
            ):
                index += 1
            if index + _JSON_REPAIR_LOOKAHEAD_OFFSET < len(raw_content):
                index += _BLOCK_COMMENT_WIDTH
            continue
        output.append(char)
        index += 1
    return "".join(output)


def _strip_trailing_commas(raw_content: str) -> str:
    output: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(raw_content):
        char = raw_content[index]
        if quote is not None:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in ('"', "'"):
            quote = char
            output.append(char)
            index += 1
            continue
        if char == ",":
            next_index = index + 1
            while next_index < len(raw_content) and raw_content[next_index].isspace():
                next_index += 1
            if next_index < len(raw_content) and raw_content[next_index] in ("}", "]"):
                index += 1
                continue
        output.append(char)
        index += 1
    return "".join(output)


def _extract_single_container(raw_content: str) -> str | None:
    starts = [idx for idx, char in enumerate(raw_content) if char in ("{", "[")]
    for start in starts:
        end = _matching_container_end(raw_content, start)
        if end is None:
            continue
        container = raw_content[start : end + 1]
        remainder = raw_content[end + 1 :]
        if any(char in ("{", "[") for char in remainder):
            return None
        prefix = raw_content[:start].strip()
        suffix = remainder.strip()
        if _looks_like_json_container(prefix) or _looks_like_json_container(suffix):
            return None
        return container
    return None


def _looks_like_json_container(text: str) -> bool:
    return text.startswith(("{", "[")) or text.endswith(("}", "]"))


def _matching_container_end(raw_content: str, start: int) -> int | None:
    opener = raw_content[start]
    closer = "}" if opener == "{" else "]"
    stack: list[str] = [closer]
    quote: str | None = None
    escaped = False
    index = start + 1
    while index < len(raw_content):
        char = raw_content[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in ('"', "'"):
            quote = char
            index += 1
            continue
        if char in ("{", "["):
            stack.append("}" if char == "{" else "]")
        elif char in ("}", "]"):
            if char != stack[-1]:
                return None
            stack.pop()
            if not stack:
                return index
        index += 1
    return None


def _coerce_json_text_container(value: object) -> object:
    """Convert JSON strings to native containers only when they decode as containers."""
    if isinstance(value, str):
        try:
            decoded = _loads_json_text_one_or_two_layers(value)
        except InvalidParamsError:
            return value
        if isinstance(decoded, (dict, list)):
            return _coerce_known_container_fields(decoded)
        return value
    return _coerce_known_container_fields(value)


def _coerce_known_container_fields(value: object) -> object:
    """Repair known dict/list container fields without changing scalar fields."""
    if isinstance(value, list):
        return [_coerce_known_container_fields(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized: dict[str, object] = {}
    for key, item in value.items():
        if key in JSON_LIST_FIELD_NAMES:
            normalized[key] = _coerce_json_list_field(item)
        elif key in JSON_CONTAINER_FIELD_NAMES or key in PLAN_SECTION_NAMES:
            normalized[key] = _coerce_json_text_container(item)
        else:
            normalized[key] = _coerce_known_container_fields(item)
    return normalized


def _coerce_json_list_field(value: object) -> object:
    """Repair common agent-produced list wrappers without dropping payload data."""
    item, unwrapped = _coerce_item_chain(value)
    if unwrapped:
        if isinstance(item, list):
            return item
        return [item]
    return item


def _coerce_item_chain(value: object) -> tuple[object, bool]:
    normalized = _coerce_json_text_container(value)
    unwrapped = False
    while isinstance(normalized, dict) and len(normalized) == 1 and "item" in normalized:
        unwrapped = True
        normalized = _coerce_json_text_container(normalized["item"])
    return normalized, unwrapped


def _coerce_artifact_envelope_content(value: object) -> object:
    """Decode only artifact-envelope ``content`` fields, not arbitrary text fields."""
    if not isinstance(value, dict):
        return value
    normalized = dict(cast("dict[str, object]", value))
    content = normalized.get("content")
    if isinstance(content, str):
        decoded = _coerce_json_text_container(content)
        if isinstance(decoded, (dict, list)):
            normalized["content"] = decoded
    return normalized


def _normalize_plan_section_payload(
    section: str,
    payload: object,
    *,
    mode: str,
) -> object:
    """Repair safe section-level JSON/container mismatches before validation."""
    normalized = _coerce_json_text_container(payload)
    if isinstance(normalized, dict):
        normalized_dict = cast("dict[str, object]", normalized)
        if len(normalized_dict) == 1 and section in normalized_dict:
            normalized = _coerce_json_text_container(normalized_dict[section])
    if (
        (section in PLAN_SECTION_LIST_ITEM_MODELS or section == "work_units")
        and isinstance(normalized, dict)
        and len(normalized) == 1
        and "item" in normalized
    ):
        normalized = _coerce_json_list_field(normalized)
    if section in PLAN_SECTION_OBJECT_MODELS and mode == "replace" and isinstance(normalized, str):
        decoded = _coerce_json_text_container(normalized)
        if isinstance(decoded, dict):
            normalized = decoded
    if (
        (section in PLAN_SECTION_LIST_ITEM_MODELS or section == "work_units")
        and mode == "replace"
        and isinstance(normalized, str)
    ):
        decoded = _coerce_json_text_container(normalized)
        if isinstance(decoded, list):
            normalized = decoded
    return _coerce_known_container_fields(normalized)


def _parse_content(raw_content: str) -> dict[str, object]:
    try:
        parsed = _parse_content_any(raw_content)
    except InvalidParamsError:
        raise
    if not isinstance(parsed, dict):
        raise InvalidParamsError("Artifact content must decode to a JSON object")
    return cast("dict[str, object]", parsed)


def _parse_content_any(raw_content: str) -> object:
    return _loads_json_text_one_or_two_layers(raw_content)


def _parse_plan_section_content(
    raw_content: object,
    *,
    section: str,
    mode: str,
    workspace_root: Path,
    backend: FileBackend,
) -> object:
    if isinstance(raw_content, str):
        try:
            parsed = _parse_content_any(raw_content)
        except InvalidParamsError as exc:
            raise InvalidParamsError(
                _format_plan_section_submission_error(
                    section=section,
                    mode=mode,
                    detail=str(exc),
                    workspace_root=workspace_root,
                    backend=backend,
                    tool_name="ralph_submit_plan_section",
                )
            ) from exc
        return _normalize_plan_section_payload(section, parsed, mode=mode)
    return _normalize_plan_section_payload(section, raw_content, mode=mode)


def _plan_format_doc_reference(workspace_root: Path, backend: FileBackend) -> str:
    relative_path = materialize_format_doc(workspace_root, PLAN_ARTIFACT_TYPE, backend=backend)
    if relative_path is not None:
        return relative_path
    return "ralph/mcp/artifacts/format_docs/plan.md"


def _format_plan_section_submission_error(
    *,
    section: str,
    mode: str,
    detail: str,
    workspace_root: Path,
    backend: FileBackend,
    tool_name: str,
) -> str:
    plan_doc = _plan_format_doc_reference(workspace_root, backend)
    guidance = [
        detail,
        f"Fix this by reading '{plan_doc}' inside the workspace.",
        (
            f"Use {tool_name} with section='{section}' and mode='{mode}'. "
            "The plan format doc sections 'Step-wise submission' and 'Detailed worked "
            "examples' show the canonical payload shapes."
        ),
        f"After fixing the payload, retry {tool_name}.",
    ]
    if section == "skills_mcp" and "mcps" in detail and "valid list" in detail:
        guidance.append(
            'Expected shape for section "skills_mcp": {"skills":["writing-plans"],"mcps":[]}. '
            'mcps must be a JSON array like [] or ["docs-mcp"].'
        )
    if (
        section == "skills_mcp"
        and "skills" in detail
        and (
            "required" in detail.lower()
            or "valid list" in detail
            or "at least one" in detail.lower()
        )
    ):
        guidance.append(
            'Expected shape for section "skills_mcp": {"skills":["writing-plans"],"mcps":[]}. '
            "skills must be a JSON array and must contain at least one task-relevant skill name."
        )
    if section == "summary" and (
        "scope_items" in detail or "must be a JSON object" in detail or "required" in detail.lower()
    ):
        guidance.append(
            'Expected shape for section "summary": {"context":"Fix the foo() regression and '
            'prove it with a focused unit test","intent":"Clamp foo() index so the regression '
            'cannot recur","intent_verb":"improve","scope_items":[{"text":"Add a regression '
            'test","category":"test"},{"text":"Modify src/foo.py","category":"file_change"},'
            '{"text":"Run pytest tests/test_foo.py -q","category":"test"}]}. summary must be a '
            "JSON object passed directly as content, not wrapped in an outer summary key."
        )
    if section == "critical_files" and "primary_files" in detail and "required" in detail.lower():
        guidance.append(
            'Expected shape for section "critical_files": {"primary_files":['
            '{"path":"src/foo.py","action":"modify"},'
            '{"path":"tests/test_foo.py","action":"modify"}],"reference_files":[]}. '
            "primary_files is required and must be a JSON array."
        )
    if section == "steps" and mode == "replace" and "must be a JSON array" in detail:
        guidance.append(
            'Expected shape for section "steps" with mode="replace": a JSON array like '
            '[{"number":1,"title":"Add a regression test","content":"Add the focused test.",'
            '"step_type":"file_change","targets":[{"path":"tests/test_foo.py","action":"modify"}],'
            '"depends_on":[],"expected_evidence":[{"kind":"test_name","ref":'
            '"tests/test_foo.py::test_clamp_handles_out_of_range_index"}]}], not a single '
            "object and not wrapped under an outer steps key."
        )
    if section == "steps" and mode == "append" and "object or array of items" in detail:
        guidance.append(
            "Expected shape for section 'steps' with mode='append': either one step object like "
            '{"number":2,"title":"Clamp the foo() index","content":"Update src/foo.py so '
            'the lookup index is clamped while preserving the public foo() signature.",'
            '"step_type":"file_change","targets":[{"path":"src/foo.py","action":"modify"}],'
            '"depends_on":[1],"expected_evidence":[{"kind":"file","ref":"src/foo.py"}]} '
            "or a JSON array of such step objects."
        )
    if section == "steps" and (
        "step_type" in detail or "verify step" in detail or "target" in detail.lower()
    ):
        guidance.append(
            'Expected shape for one steps item: {"number":1,"title":"Add the foo() regression '
            'test","content":"Add tests/test_foo.py::test_clamp_handles_out_of_range_index '
            'before changing production code.","step_type":"file_change","targets":['
            '{"path":"tests/test_foo.py","action":"modify"}],"depends_on":[],'
            '"expected_evidence":[{"kind":"test_name","ref":'
            '"tests/test_foo.py::test_clamp_handles_out_of_range_index"}]}. '
            "For verify steps, "
            "use step_type='verify' plus verify_command or location."
        )
    if section == "risks_mitigations" and mode == "replace" and "must be a JSON array" in detail:
        guidance.append(
            'Expected shape for section "risks_mitigations" with mode="replace": a JSON array '
            'like [{"risk":"Clamping could hide a caller bug that should remain visible in '
            'behavior expectations.","mitigation":"Preserve the public foo() signature and add '
            'a focused regression test documenting the intended behavior.","severity":"medium"}].'
        )
    if section == "risks_mitigations" and mode == "append" and "object or array of items" in detail:
        guidance.append(
            "Expected shape for section 'risks_mitigations' with mode='append': either one "
            "object like "
            '{"risk":"Clamping could hide a caller bug that should remain visible in behavior '
            'expectations.","mitigation":"Preserve the public foo() signature and add a focused '
            'regression test documenting the intended behavior.","severity":"medium"} '
            "or a JSON array of such risk objects."
        )
    if section == "verification_strategy" and (
        (mode == "replace" and "must be a JSON array" in detail)
        or (mode == "append" and "object or array of items" in detail)
        or "method" in detail
        or "expected_outcome" in detail
    ):
        guidance.append(
            'Expected shape for section "verification_strategy": [{"method":'
            '"pytest tests/test_foo.py -q","expected_outcome":"The focused foo() regression '
            'test passes.","timeout_seconds":60,"cwd":"ralph-workflow"}]. With mode="replace" '
            "use a JSON array, not an object wrapped under verification_strategy; with "
            'mode="append" use one '
            "verification object or a JSON array of verification objects."
        )
    if (
        section in {"steps", "risks_mitigations", "verification_strategy"}
        and "Content must be valid JSON" in detail
    ):
        guidance.append(
            f'Expected shape for section "{section}" with mode="{mode}": JSON array like '
            '[{"number":1,"title":"Add a regression test","content":"Add the focused test."}]. '
            "Fix the JSON syntax first (for example the parser is often "
            "missing a comma or closing brace)."
        )
    guidance.append(
        "Optional: the bundled `submit-plan-artifact` skill shows the canonical section "
        "envelopes and detailed passing plan examples."
    )
    return " ".join(guidance)


def _format_plan_batch_envelope_error(
    *,
    detail: str,
    workspace_root: Path,
    backend: FileBackend,
) -> str:
    plan_doc = _plan_format_doc_reference(workspace_root, backend)
    return " ".join(
        [
            detail,
            f"Fix this by reading '{plan_doc}' inside the workspace.",
            (
                "Use ralph_submit_plan_sections with the canonical batch envelope "
                '{"entries":[{"section":"summary","mode":"replace","content":{"context":'
                '"Fix foo() out-of-range index handling after reading src/foo.py and '
                'tests/test_foo.py","intent":"Clamp foo() indexes and prove the '
                'regression with a focused test","intent_verb":"fix","scope_items":'
                '[{"text":"Add tests/test_foo.py::test_clamp_handles_out_of_range_index",'
                '"category":"test"},{"text":"Update src/foo.py to clamp negative and '
                'oversized indexes without changing the public foo() signature",'
                '"category":"bugfix"},{"text":"Run pytest tests/test_foo.py -q to prove the '
                'regression is fixed","category":"test"}]}}]}. '
                "The plan format doc section 'Step-wise submission' shows the valid section names, "
                "content shapes, and mode usage."
            ),
            "After fixing the batch payload, retry ralph_submit_plan_sections.",
            "Optional: the bundled `submit-plan-artifact` skill shows the canonical batch "
            "envelope and detailed passing plan examples.",
        ]
    )


def _format_plan_finalize_error(
    *,
    detail: str,
    workspace_root: Path,
    backend: FileBackend,
    tool_name: str,
) -> str:
    plan_doc = _plan_format_doc_reference(workspace_root, backend)
    return " ".join(
        [
            detail,
            f"Fix this by reading '{plan_doc}' inside the workspace.",
            (
                f"Update the staged draft with ralph_submit_plan_section, "
                "ralph_submit_plan_sections, or the plan step-edit tools, then retry "
                f"{tool_name}. The plan format doc sections 'Step-wise submission' and "
                "'Dumb-proof checklist' show the required sections and valid payload shapes."
            ),
            (
                "Canonical required section shapes: "
                'summary={"context":"Fix the foo() regression and prove it with a focused '
                'unit test","intent":"Clamp foo() index so the regression cannot recur",'
                '"intent_verb":"improve","scope_items":[{"text":"Add a regression test",'
                '"category":"test"},{"text":"Modify src/foo.py","category":"file_change"},'
                '{"text":"Run pytest tests/test_foo.py -q","category":"test"}]}; '
                'skills_mcp={"skills":["writing-plans"],"mcps":[]}; '
                'steps=[{"number":1,"title":"Add the foo() regression test","content":'
                '"Add tests/test_foo.py::test_clamp_handles_out_of_range_index before '
                'changing production code.","step_type":"file_change","targets":['
                '{"path":"tests/test_foo.py","action":"modify"}],"depends_on":[],'
                '"expected_evidence":[{"kind":"test_name","ref":'
                '"tests/test_foo.py::test_clamp_handles_out_of_range_index"}]}]; '
                'critical_files={"primary_files":[{"path":"src/foo.py","action":"modify"},'
                '{"path":"tests/test_foo.py","action":"modify"}],"reference_files":[]}; '
                'risks_mitigations=[{"risk":"Clamping could hide a caller bug that should '
                'remain visible in behavior expectations.","mitigation":"Preserve the public '
                'foo() signature and add a focused regression test documenting the intended '
                'clamping behavior.","severity":"medium"}]; '
                'verification_strategy=[{"method":"pytest tests/test_foo.py -q",'
                '"expected_outcome":"The focused foo() regression test passes."}].'
            ),
            (
                "Optional: the bundled `submit-plan-artifact` skill shows the canonical "
                "required-section shapes and detailed passing plan examples."
            ),
        ]
    )


def _format_plan_step_edit_error(
    *,
    detail: str,
    workspace_root: Path,
    backend: FileBackend,
    tool_name: str,
) -> str:
    plan_doc = _plan_format_doc_reference(workspace_root, backend)
    return " ".join(
        [
            detail,
            f"Fix this by reading '{plan_doc}' inside the workspace.",
            (
                f"Use {tool_name} with the current step numbers from ralph_get_plan_draft. "
                "The plan format doc sections 'Step-mutation read-after-write echo' and "
                "'Step-wise submission' show the valid step payload shape and numbering flow."
            ),
            (
                "Canonical step-edit envelopes: ralph_insert_plan_step => "
                '{"index":2,"step":{"number":2,"title":"Clamp the foo() index",'
                '"content":"Update src/foo.py so foo() clamps out-of-range indexes.",'
                '"step_type":"file_change","targets":[{"path":"src/foo.py","action":"modify"}]}}; '
                'ralph_replace_plan_step => {"step_number":2,"step":{"number":2,'
                '"title":"Clamp the foo() index","content":"Update src/foo.py so foo() '
                'clamps out-of-range indexes.","step_type":"file_change","targets":'
                '[{"path":"src/foo.py","action":"modify"}]}}; '
                'ralph_remove_plan_step => {"step_number":2}; '
                'ralph_move_plan_step => {"from_step_number":2,"to_index":1}; '
                'ralph_patch_step => {"step_number":2,"step":{"content":"Preserve '
                "valid-index behavior and add expected evidence.\"}}."
            ),
            f"After fixing the payload or step number, retry {tool_name}.",
            (
                "Per-tool guidance: the bundled submit-plan-step-edits skill documents the exact "
                "retry envelope and reindex semantics for ralph_insert_plan_step, "
                "ralph_replace_plan_step, ralph_patch_step, ralph_remove_plan_step, and "
                "ralph_move_plan_step."
            ),
            (
                "Optional: the bundled `submit-plan-step-edits` skill shows analysis-feedback "
                "correction examples for replacing, patching, and inserting detailed steps."
            ),
            (
                "For full-section planning repairs, the bundled `submit-plan-artifact` skill "
                "shows complete section payloads and detailed passing plan examples."
            ),
        ]
    )


def _parse_plan_content(raw_content: str) -> dict[str, object]:
    """Strict envelope-aware plan payload decoder.

    Delegates to ``parse_plan_payload_strict`` so the four previously
    duplicated JSON parsers share a single source of truth. The strict
    helper raises ``PlanArtifactValidationError`` on invalid JSON or a
    malformed envelope, which we translate to ``InvalidParamsError`` so
    the MCP tool error path stays consistent.
    """
    try:
        decoded = _parse_content_any(raw_content)
        if isinstance(decoded, dict):
            normalized = _coerce_artifact_envelope_content(decoded)
            return parse_plan_payload_strict(cast("dict[str, object]", normalized))
        if isinstance(decoded, str):
            return parse_plan_payload_strict(decoded)
        raise PlanArtifactValidationError("plan payload must decode to a JSON object")
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
    decoded = cast("dict[str, object]", _coerce_artifact_envelope_content(decoded))
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
        "Fix the payload, then retry ralph_submit_artifact. Do NOT rely on the raw error text. "
        "Optional: the bundled `submit-artifact` skill shows the canonical envelope for "
        "each non-plan artifact type."
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
                    "Fix the artifact_type, then retry ralph_submit_artifact. "
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

    if isinstance(raw_content, (dict, list)):
        return json.dumps(raw_content)

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
    per_type_sentence = _per_type_skill_pointer_sentence(artifact_type)
    return (
        "Artifact submission requires the 'content' field. "
        "Use 'content' with a freshly generated JSON string. "
        "Do not use 'content_path' in agent-facing artifact submissions. "
        f"Example submit: {fresh_submit_example}. "
        "Optional: the bundled `submit-artifact` skill shows the canonical envelope for "
        "each non-plan artifact type."
        f"{per_type_sentence}"
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
    parsed_content = cast(
        "dict[str, object]",
        _coerce_known_container_fields(parsed_content),
    )
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
    detail = str(original_exc).strip() or "<missing validation detail>"
    prefix = "Validation detail: "
    if detail.startswith("Artifact '") and prefix in detail:
        detail = detail.split(prefix, 1)[1]
        detail = detail.split(". The exact format is documented at", 1)[0]
        detail = detail.split(". (note: could not write the reference file;", 1)[0]
    detail = detail.replace(
        "Field required; field is required and must be provided",
        "required field is missing",
    )
    if artifact_type == PLAN_ARTIFACT_TYPE:
        plan_doc = _plan_format_doc_reference(workspace_root, backend)
        msg = (
            f"Artifact '{artifact_type}' failed validation. "
            f"Validation detail: {detail}. "
            f"The exact format is documented at '{plan_doc}' inside the workspace. "
            "Do not retry the generic ralph_submit_artifact path for plans. "
            "Stage or repair the draft with ralph_submit_plan_section, "
            "ralph_submit_plan_sections, or the plan step-edit tools; run "
            "ralph_validate_draft; then retry ralph_finalize_plan. "
            "Valid JSON that is not yet schema-valid should remain staged with "
            "validation_warnings so the strict validator can report the remaining "
            "plan issues without losing data. "
            "Optional: the bundled `submit-plan-artifact` skill shows the canonical "
            "section envelopes and detailed passing plan examples."
        )
        raise InvalidParamsError(msg) from original_exc

    per_type_sentence = _per_type_skill_pointer_sentence(artifact_type)
    try:
        relative_path = materialize_format_doc(workspace_root, artifact_type, backend=backend)
        if relative_path is not None:
            msg = (
                f"Artifact '{artifact_type}' failed validation. "
                f"Validation detail: {detail}. "
                f"The exact format is documented at '{relative_path}' inside the workspace. "
                "Read that file and rebuild your submission before retrying. "
                "Then retry ralph_submit_artifact. "
                "Use artifact_type set to the same value you just submitted and "
                "content set to a native JSON object or JSON string rebuilt from the format doc. "
                "Do NOT rely on guesswork; follow the documented shape exactly. "
                "Optional: the bundled `submit-artifact` skill shows the canonical "
                "envelope for each non-plan artifact type."
                f"{per_type_sentence}"
            )
        else:
            msg = (
                f"Artifact '{artifact_type}' failed validation. "
                f"Validation detail: {detail}. "
                f"(note: could not write the reference file; "
                f"read 'ralph/mcp/artifacts/format_docs/{artifact_type}.md' "
                "in the repo source tree "
                "instead, rebuild the submission before retrying, then retry "
                "ralph_submit_artifact with the same artifact_type and rebuilt content.) "
                "Optional: the bundled `submit-artifact` skill shows the canonical "
                "envelope for each non-plan artifact type."
                f"{per_type_sentence}"
            )
    except OSError:
        msg = (
            f"Artifact '{artifact_type}' failed validation. "
            f"Validation detail: {detail}. "
            f"(note: could not write the reference file; "
            f"read 'ralph/mcp/artifacts/format_docs/{artifact_type}.md' "
            "in the repo source tree "
            "instead, rebuild the submission before retrying, then retry "
            "ralph_submit_artifact with the same artifact_type and rebuilt content.) "
            "Optional: the bundled `submit-artifact` skill shows the canonical "
            "envelope for each non-plan artifact type."
            f"{per_type_sentence}"
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
