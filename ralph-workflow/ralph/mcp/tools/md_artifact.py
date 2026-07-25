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

if TYPE_CHECKING:
    from pathlib import Path

_PLAN_READ_CAPABILITY = "artifact.plan_read"


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
    """Validate and canonically persist a markdown artifact atomically."""
    require_capability(session, ARTIFACT_SUBMIT_CAPABILITY, "Markdown artifact submission")
    artifact_type, content = _params(params)
    parsed_content, diagnostics, overridden = _parse_with_overrides(artifact_type, content)
    result = _validation_result(artifact_type, diagnostics, overridden)
    if result.is_error:
        return result
    _submit_canonical(session, workspace, artifact_type, parsed_content, content, deps)
    return result


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
    if existing is None or not existing:
        draft = content
    elif existing.endswith("\n"):
        draft = existing + content
    else:
        draft = f"{existing}\n{content}"
    cap = md_draft_character_cap(get_spec(artifact_type))
    if len(draft) > cap:
        raise InvalidParamsError(
            f"staged draft for {artifact_type!r} would exceed its character cap "
            f"({len(draft)} > {cap}); the existing draft is unchanged"
        )
    save_md_draft(artifact_dir, artifact_type, draft, backend=backend)
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
    """Drop the persisted markdown draft for one artifact type."""
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

    On validation failure the draft is kept intact for repair and the result
    carries the exact diagnostics ``ralph_submit_md_artifact`` would produce.
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
    delete_md_draft(artifact_dir, artifact_type, backend=backend)
    return result


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


def _draft_status_result(
    artifact_type: str, draft: str, *, exists: bool | None = None
) -> ToolResult:
    """Describe the draft-so-far: length, section outline, check-only diagnostics.

    Diagnostics come from the same validator submission uses, but never gate
    the result — a partial document is expected to report missing sections.
    Staging omits the ``exists``/``content`` echo; ``ralph_get_md_draft``
    passes ``exists`` and gets the full draft back for resumption.
    """
    document, _ = parse_markdown_document(draft)
    diagnostics, overridden = _validate_with_overrides(artifact_type, draft)
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
    return ToolResult(content=[ToolContent.json_content(payload)], is_error=False)


def _validation_result(
    artifact_type: str,
    diagnostics: list[Diagnostic],
    overridden: list[object] | None = None,
) -> ToolResult:
    return ToolResult(
        content=[
            ToolContent.json_content(
                {
                    "artifact_type": artifact_type,
                    "valid": not any(item.severity == "error" for item in diagnostics),
                    "diagnostics": [_diagnostic_payload(item) for item in diagnostics],
                    "counts": _severity_counts(diagnostics),
                    "overridden": [
                        _override_payload(item)
                        for item in (overridden or [])
                    ],
                }
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
