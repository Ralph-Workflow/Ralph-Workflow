"""MCP handlers for validated markdown artifact documents."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.canonical_submit import submit_artifact_canonical
from ralph.mcp.artifacts.markdown import Diagnostic, parse_and_validate, parse_markdown_document
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.markdown.specs.plan import (
    _OverrideMatch,
    analyze_plan_document,
)
from ralph.mcp.artifacts.md_draft_io import (
    delete_md_draft,
    is_md_draft_seeded,
    load_md_draft,
    md_draft_character_cap,
    save_md_draft,
)
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
from ralph.mcp.tools.names import (
    EDIT_MD_ARTIFACT_TOOL,
    FINALIZE_MD_ARTIFACT_TOOL,
    GET_MD_DRAFT_TOOL,
)
from ralph.mcp.tools.text_edits import (
    RejectedTextEdits,
    apply_text_edits,
    parse_text_edits,
    sha256_text,
)

if TYPE_CHECKING:
    from pathlib import Path

_PLAN_READ_CAPABILITY = "artifact.plan_read"

#: Every validating endpoint returns this so a rejected document is repaired
#: in place rather than re-transcribed in full. Submission always leaves the
#: submitted text staged as the draft, so the repair path is available even
#: after a one-shot whole-document submit.
REPAIR_HINT: str = (
    f"The submitted document is staged as this artifact's draft. Repair it in place with "
    f"`{EDIT_MD_ARTIFACT_TOOL}` (oldText/newText edits) instead of resending the whole "
    f"document; it resubmits the artifact automatically as soon as the edited draft "
    f"validates, so no separate `{FINALIZE_MD_ARTIFACT_TOOL}` call is needed. Read the draft "
    f"back with `{GET_MD_DRAFT_TOOL}` when you need to see its current text."
)


def handle_verify_md_artifact(
    session: CoordinationSessionLike,
    _workspace: WorkspaceLike,
    params: dict[str, object],
) -> ToolResult:
    """Check a markdown artifact without writing it."""
    require_capability(session, _PLAN_READ_CAPABILITY, "Markdown artifact verification")
    artifact_type, content = _params(params)
    diagnostics, overridden = _validate_with_overrides(artifact_type, content)
    return _validation_result(artifact_type, diagnostics, overridden)


def handle_submit_md_artifact(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Validate and canonically persist a markdown artifact atomically.

    The submitted text is staged as the artifact's draft first, whether or
    not it validates, so a rejected document can be repaired in place with
    ``ralph_edit_md_artifact`` rather than re-transcribed in full.
    """
    require_capability(session, ARTIFACT_SUBMIT_CAPABILITY, "Markdown artifact submission")
    artifact_type, content = _params(params)
    _write_draft(session, workspace, artifact_type, content, deps)
    parsed_content, diagnostics, overridden = _parse_with_overrides(artifact_type, content)
    result = _validation_result(artifact_type, diagnostics, overridden)
    if result.is_error:
        return result
    _submit_canonical(session, workspace, artifact_type, parsed_content, content, deps)
    return _submitted_validation_result(artifact_type, content, diagnostics, overridden)


def handle_edit_md_artifact(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Apply oldText/newText edits to the staged draft and submit it when it validates.

    This is ``ralph_submit_md_artifact`` starting from the existing draft
    instead of a whole retyped document: whenever the edited draft passes the
    submission gate it is persisted through the same canonical path submission
    uses, with no separate finalize call. An edited draft that still has errors
    is kept as a draft for further repair and nothing is submitted. Submission
    does not end the phase — ``declare_complete`` remains separate — so a later
    edit in the same phase attempt simply resubmits.

    Edits apply sequentially, each replacing the first occurrence of its
    ``oldText``; a single miss rejects the whole batch and writes nothing.
    ``dry_run`` previews the diff without persisting or submitting, and
    ``expected_content_hash`` fails closed when the draft changed underneath
    the caller. The response carries the refreshed draft diagnostics and a
    ``submitted`` flag, so one call shows what changed, whether the artifact
    now validates, and whether it was submitted.
    """
    require_capability(session, ARTIFACT_SUBMIT_CAPABILITY, "Markdown draft editing")
    artifact_type = _artifact_type_param(params)
    edits = parse_text_edits(params)
    backend = (deps or DEFAULT_ARTIFACT_HANDLER_DEPS).backend
    artifact_dir = _resolve_artifact_dir(session, workspace)
    draft = load_md_draft(artifact_dir, artifact_type, backend=backend)
    if draft is None:
        raise InvalidParamsError(
            f"no staged draft for {artifact_type!r}; stage or submit content first"
        )

    expected_hash = params.get("expected_content_hash")
    if isinstance(expected_hash, str):
        current_hash = sha256_text(draft)
        if current_hash != expected_hash:
            return _stale_draft_result(artifact_type, expected_hash, current_hash)

    label = f".{artifact_type}.draft.md"
    outcome = apply_text_edits(draft, edits, label=label)
    if isinstance(outcome, RejectedTextEdits):
        return ToolResult(
            content=[ToolContent.json_content(_with_hint(outcome.payload))],
            is_error=True,
        )

    cap = md_draft_character_cap(get_spec(artifact_type))
    if len(outcome.content) > cap:
        raise InvalidParamsError(
            f"edited draft for {artifact_type!r} would exceed its character cap "
            f"({len(outcome.content)} > {cap}); the existing draft is unchanged"
        )

    if bool(params.get("dry_run", False)):
        return _edit_result(
            artifact_type, draft, outcome.diff, len(outcome.applied), "preview", submitted=False
        )

    save_md_draft(artifact_dir, artifact_type, outcome.content, backend=backend)
    parsed_content, diagnostics, overridden = _parse_with_overrides(artifact_type, outcome.content)
    submitted = not any(item.severity == "error" for item in diagnostics)
    if submitted:
        _submit_canonical(session, workspace, artifact_type, parsed_content, outcome.content, deps)
    return _edit_result(
        artifact_type,
        outcome.content,
        outcome.diff,
        len(outcome.applied),
        "applied",
        submitted=submitted,
        analysis=(diagnostics, overridden),
    )


def handle_stage_md_artifact(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Append to (or replace) a persisted markdown draft without gating on validity."""
    require_capability(session, ARTIFACT_SUBMIT_CAPABILITY, "Markdown draft staging")
    artifact_type, content = _params(params)
    mode = params.get("mode", "append")
    if mode not in ("append", "replace_all"):
        raise InvalidParamsError("mode must be 'append' or 'replace_all'")
    backend = (deps or DEFAULT_ARTIFACT_HANDLER_DEPS).backend
    artifact_dir = _resolve_artifact_dir(session, workspace)
    existing = (
        load_md_draft(artifact_dir, artifact_type, backend=backend) if mode == "append" else None
    )
    if existing and mode == "append" and is_md_draft_seeded(
        artifact_dir, artifact_type, backend=backend
    ):
        raise InvalidParamsError(
            "draft was restored from the last submitted artifact; use mode='replace_all' "
            f"for a new document or `{EDIT_MD_ARTIFACT_TOOL}` to repair it in place"
        )
    if existing is None or not existing:
        draft = content
    elif existing.endswith("\n"):
        draft = existing + content
    else:
        draft = f"{existing}\n{content}"
    _write_draft(session, workspace, artifact_type, draft, deps, label="staged")
    return _draft_status_result(artifact_type, draft)


def handle_get_md_draft(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Return the persisted draft and its diagnostics for resumable authoring."""
    require_capability(session, _PLAN_READ_CAPABILITY, "Markdown draft inspection")
    artifact_type = _artifact_type_param(params)
    backend = (deps or DEFAULT_ARTIFACT_HANDLER_DEPS).backend
    draft = load_md_draft(_resolve_artifact_dir(session, workspace), artifact_type, backend=backend)
    return _draft_status_result(artifact_type, draft or "", exists=draft is not None)


def handle_discard_md_draft(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Drop the persisted markdown draft for one artifact type.

    Warning:
        This discards the whole document and forces a full retype. It is not
        the way to recover from validation errors, nor to make a revision whose
        content is substantially similar to the draft — ``ralph_edit_md_artifact``
        repairs in place and submits once the draft validates. Reserve discard
        for a genuine wholesale restart where almost nothing survives.
    """
    require_capability(session, ARTIFACT_SUBMIT_CAPABILITY, "Markdown draft discard")
    artifact_type = _artifact_type_param(params)
    backend = (deps or DEFAULT_ARTIFACT_HANDLER_DEPS).backend
    discarded = delete_md_draft(
        _resolve_artifact_dir(session, workspace), artifact_type, backend=backend
    )
    return ToolResult(
        content=[
            ToolContent.json_content({"artifact_type": artifact_type, "discarded": discarded})
        ],
        is_error=False,
    )


def handle_finalize_md_artifact(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    params: dict[str, object],
    *,
    deps: ArtifactHandlerDeps | None = None,
) -> ToolResult:
    """Validate the assembled draft with the submission gate and submit it canonically.

    The draft is retained either way: on failure so it can be repaired, and
    on success so the submitted document remains the editable substrate for a
    later revision within the same phase attempt. Fresh phase entry — not
    submission — is what clears it.
    """
    require_capability(session, ARTIFACT_SUBMIT_CAPABILITY, "Markdown draft finalization")
    artifact_type = _artifact_type_param(params)
    backend = (deps or DEFAULT_ARTIFACT_HANDLER_DEPS).backend
    artifact_dir = _resolve_artifact_dir(session, workspace)
    content = load_md_draft(artifact_dir, artifact_type, backend=backend)
    if content is None:
        raise InvalidParamsError(
            f"no staged draft for {artifact_type!r}; stage content first "
            "or submit the complete document directly"
        )
    parsed_content, diagnostics, overridden = _parse_with_overrides(artifact_type, content)
    result = _validation_result(artifact_type, diagnostics, overridden)
    if result.is_error:
        return result
    _submit_canonical(session, workspace, artifact_type, parsed_content, content, deps)
    return _submitted_validation_result(artifact_type, content, diagnostics, overridden)


def _submit_canonical(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    artifact_type: str,
    parsed_content: dict[str, object],
    content: str,
    deps: ArtifactHandlerDeps | None,
) -> None:
    """Persist one validated markdown document through the canonical path."""
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
    worker_namespace = cast(
        "Path | None", getattr(session, "worker_namespace", None)
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    worker_artifact_dir = cast(
        "Path | None", getattr(session, "worker_artifact_dir", None)
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    if worker_namespace is None and worker_artifact_dir is not None:
        worker_namespace = worker_artifact_dir.parent
    submit_artifact_canonical(
        workspace_root=workspace_root,
        artifact_type=artifact_type,
        parsed_content=parsed_content,
        markdown=content,
        deps=effective_deps,
        run_id=_session_run_id(session),
        artifact_dir=_resolve_artifact_dir(session, workspace),
        handoff_dir=worker_namespace / "handoffs" if worker_namespace is not None else None,
    )


def _params(params: dict[str, object]) -> tuple[str, str]:
    artifact_type = _artifact_type_param(params)
    content = params.get("content")
    if not isinstance(content, str):
        raise InvalidParamsError("Missing 'content' markdown parameter")
    return artifact_type, content


def _artifact_type_param(params: dict[str, object]) -> str:
    import_module("ralph.mcp.artifacts.markdown.specs")
    artifact_type = params.get("artifact_type")
    if not isinstance(artifact_type, str) or not artifact_type:
        raise InvalidParamsError("Missing 'artifact_type' parameter")
    try:
        get_spec(artifact_type)
    except ValueError as exc:
        raise InvalidParamsError(str(exc)) from exc
    return artifact_type


def _write_draft(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    artifact_type: str,
    content: str,
    deps: ArtifactHandlerDeps | None,
    *,
    label: str = "submitted",
) -> None:
    """Persist ``content`` as the artifact's draft, enforcing the character cap.

    Raises:
        InvalidParamsError: when the content exceeds the artifact's draft
            cap. The existing draft is left untouched.
    """
    cap = md_draft_character_cap(get_spec(artifact_type))
    if len(content) > cap:
        raise InvalidParamsError(
            f"{label} draft for {artifact_type!r} would exceed its character cap "
            f"({len(content)} > {cap}); the existing draft is unchanged"
        )
    backend = (deps or DEFAULT_ARTIFACT_HANDLER_DEPS).backend
    save_md_draft(
        _resolve_artifact_dir(session, workspace), artifact_type, content, backend=backend
    )


def _with_hint(payload: dict[str, object]) -> dict[str, object]:
    """Attach the in-place repair hint to a tool payload."""
    payload["repair_hint"] = REPAIR_HINT
    return payload


def _stale_draft_result(artifact_type: str, expected: str, current: str) -> ToolResult:
    """Report a draft that changed since the caller last read it."""
    return ToolResult(
        content=[
            ToolContent.json_content(
                _with_hint(
                    {
                        "status": "stale_evidence",
                        "artifact_type": artifact_type,
                        "expected_content_hash": expected,
                        "current_content_hash": current,
                        "reason": "content_changed",
                    }
                )
            )
        ],
        is_error=True,
    )


def _edit_result(
    artifact_type: str,
    draft: str,
    diff: str,
    edits_applied: int,
    status: str,
    *,
    submitted: bool,
    analysis: tuple[list[Diagnostic], list[object]] | None = None,
) -> ToolResult:
    """Return the edit outcome alongside the refreshed draft diagnostics.

    ``submitted`` reports whether the edited draft went through the canonical
    submission path, so a green result is never mistaken for a submission that
    did not happen. ``analysis`` reuses the diagnostics the submit decision was
    already made from rather than validating the same draft a second time.
    """
    payload = _draft_status_payload(artifact_type, draft, analysis=analysis)
    payload["status"] = status
    payload["diff"] = diff
    payload["edits_applied"] = edits_applied
    payload["submitted"] = submitted
    if submitted:
        payload["persisted_document"] = _document_summary(draft)
    return ToolResult(content=[ToolContent.json_content(payload)], is_error=False)


def _draft_status_payload(
    artifact_type: str,
    draft: str,
    *,
    exists: bool | None = None,
    analysis: tuple[list[Diagnostic], list[object]] | None = None,
) -> dict[str, object]:
    """Describe the draft-so-far: length, section outline, check-only diagnostics.

    Diagnostics come from the same validator submission uses, but never gate
    the result — a partial document is expected to report missing sections.
    Staging omits the ``exists``/``content`` echo; ``ralph_get_md_draft``
    passes ``exists`` and gets the full draft back for resumption.
    """
    document, _ = parse_markdown_document(draft)
    diagnostics, overridden = analysis or _validate_with_overrides(artifact_type, draft)
    payload: dict[str, object] = {"artifact_type": artifact_type}
    if exists is not None:
        payload["exists"] = exists
        payload["content"] = draft
    payload.update(
        draft_chars=len(draft),
        sections=[section.name for section in document.sections],
        valid=not any(item.severity == "error" for item in diagnostics),
        diagnostics=[_diagnostic_payload(item) for item in diagnostics],
        counts=_severity_counts(diagnostics),
        overridden=[_override_payload(item) for item in overridden],
    )
    return _with_hint(payload)


def _draft_status_result(
    artifact_type: str, draft: str, *, exists: bool | None = None
) -> ToolResult:
    """Return the draft-status payload as a non-gating tool result."""
    return ToolResult(
        content=[
            ToolContent.json_content(_draft_status_payload(artifact_type, draft, exists=exists))
        ],
        is_error=False,
    )


def _document_summary(content: str) -> dict[str, object]:
    """Return the persisted document outline without reparsing validation."""
    document, _ = parse_markdown_document(content)
    return {
        "characters": len(content),
        "sections": [section.name for section in document.sections],
        "step_ids": [block.identifier for section in document.sections for block in section.blocks],
    }


def _submitted_validation_result(
    artifact_type: str,
    content: str,
    diagnostics: list[Diagnostic],
    overridden: list[object] | None = None,
) -> ToolResult:
    """Return successful validation plus the exact document summary persisted."""
    result = _validation_result(artifact_type, diagnostics, overridden)
    payload = _with_hint(
        {
            "artifact_type": artifact_type,
            "valid": True,
            "diagnostics": [_diagnostic_payload(item) for item in diagnostics],
            "counts": _severity_counts(diagnostics),
            "overridden": [_override_payload(item) for item in (overridden or [])],
            "persisted_document": _document_summary(content),
        }
    )
    return ToolResult(content=[ToolContent.json_content(payload)], is_error=result.is_error)


def _validation_result(
    artifact_type: str,
    diagnostics: list[Diagnostic],
    overridden: list[object] | None = None,
) -> ToolResult:
    return ToolResult(
        content=[
            ToolContent.json_content(
                _with_hint(
                    {
                        "artifact_type": artifact_type,
                        "valid": not any(item.severity == "error" for item in diagnostics),
                        "diagnostics": [_diagnostic_payload(item) for item in diagnostics],
                        "counts": _severity_counts(diagnostics),
                        "overridden": [_override_payload(item) for item in (overridden or [])],
                    }
                )
            )
        ],
        is_error=any(item.severity == "error" for item in diagnostics),
    )


def _validate_with_overrides(
    artifact_type: str, content: str
) -> tuple[list[Diagnostic], list[object]]:
    """Return the diagnostics and override list for a draft, plan-aware.

    For plan artifacts, ``analyze_plan_document`` applies the override ledger
    and exposes the matched-overrides list. For other artifact types, no
    overrides exist and the standard parse_and_validate diagnostics are
    returned with an empty override list.
    """
    if artifact_type == "plan":
        _, diagnostics, overridden = analyze_plan_document(content)
        return diagnostics, list(overridden)
    _, diagnostics = parse_and_validate(content, get_spec(artifact_type))
    return diagnostics, []


def _parse_with_overrides(
    artifact_type: str, content: str
) -> tuple[dict[str, object], list[Diagnostic], list[object]]:
    """Return content, diagnostics, and overrides — plan-aware.

    The content is the canonical mapping when validation passes; an empty
    dict when the artifact fails validation. Tool callers use the same
    shape whether they got the content from parse_and_validate (other
    specs) or analyze_plan_document (plan).
    """
    if artifact_type == "plan":
        parsed_content, diagnostics, overridden = analyze_plan_document(content)
        return parsed_content, diagnostics, list(overridden)
    parsed_content, diagnostics = parse_and_validate(content, get_spec(artifact_type))
    return parsed_content, diagnostics, []


def _severity_counts(diagnostics: list[Diagnostic]) -> dict[str, int]:
    """Return per-severity counts so callers can see the warning/Info load at a glance."""
    counts = {"error": 0, "warning": 0, "info": 0}
    for diagnostic in diagnostics:
        if diagnostic.severity in counts:
            counts[diagnostic.severity] += 1
    return counts


def _diagnostic_payload(diagnostic: Diagnostic) -> dict[str, object]:
    """Serialize one Diagnostic for the tool response with explicit field types.

    Hand-built to avoid ``dataclasses.asdict``'s ``dict[str, Any]`` return
    type colliding with the strict ``disallow_any_expr`` mypy policy; the
    JSON contract is unchanged.
    """
    return {
        "line": diagnostic.line,
        "section": diagnostic.section,
        "rule_id": diagnostic.rule_id,
        "message": diagnostic.message,
        "severity": diagnostic.severity,
    }


def _override_payload(match: object) -> dict[str, object]:
    """Serialize an OverrideMatch dataclass for the tool response.

    The tool layer accepts any object the plan validator's analyze entry point
    produces; we narrow to the OverrideMatch shape via dataclass detection and
    fall back to a minimal payload for unexpected objects so the tool never
    raises a serialization error on the hot path.
    """
    if isinstance(match, _OverrideMatch):
        diagnostic_dict: dict[str, object] = (
            _diagnostic_payload(match.diagnostic) if match.diagnostic is not None else {}
        )
        return {
            "rule_id": match.rule_id,
            "section": match.section,
            "reason": match.reason,
            "diagnostic": diagnostic_dict,
        }
    return cast("dict[str, object]", match)


__all__ = [
    "handle_discard_md_draft",
    "handle_finalize_md_artifact",
    "handle_get_md_draft",
    "handle_stage_md_artifact",
    "handle_submit_md_artifact",
    "handle_verify_md_artifact",
]
