"""Diagnostic helpers split out of :mod:`ralph.mcp.tools.md_artifact`.

Pure refactor: every helper that lived in ``md_artifact.py`` lines 712+ now
lives here so the public tool module stays under the repo-structure
``MAX_FILE_LINES`` cap. The callers inside ``md_artifact`` import the names
from this module; nothing about the public tool surface changes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present
from ralph.mcp.artifacts.markdown import Diagnostic, parse_and_validate, parse_markdown_document
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.markdown.specs._plan_steps import step_number_map
from ralph.mcp.artifacts.markdown.specs.plan import _OverrideMatch, analyze_plan_document
from ralph.mcp.artifacts.plan_item_proof import is_ui_plan_item
from ralph.mcp.multimodal.resources import parse_media_uri
from ralph.mcp.server._wire_ledger import params_digest, wire_evidence_for
from ralph.mcp.tools._development_result_session_gate import development_result_session_diagnostics
from ralph.mcp.tools.artifact import (
    DEFAULT_ARTIFACT_HANDLER_DEPS,
    ArtifactHandlerDeps,
    _resolve_artifact_dir,
    _session_run_id,
    _workspace_root,
)
from ralph.pipeline.work_units import (
    WorkUnitsValidationError,
    parse_work_units_from_artifact,
)
from ralph.policy.loader import load_policy
from ralph.policy.validation import (
    PolicyValidationError,
    validate_work_units_against_policy,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.tools.coordination import CoordinationSessionLike, WorkspaceLike
    from ralph.policy.models import PipelinePolicy


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


def _parse_with_overrides(
    artifact_type: str, content: str
) -> tuple[dict[str, object], list[Diagnostic], list[object]]:
    """Parse an artifact's content, routing plans through the plan validator.

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


def _work_units_policy_check(
    workspace: WorkspaceLike,
    artifact_type: str,
    parsed_content: dict[str, object],
    content: str,
) -> list[Diagnostic]:
    """Validate plan work_units against the workspace's effective pipeline policy.

    Returns an empty list when the artifact is not a plan, when no work_units
    are declared, when the structural validator already rejected them, or
    when the workspace's policy cannot be loaded. The latter matches the
    fail-open stance of ``_resolve_history_enabled`` so artifact
    verification does not depend on policy I/O.

    Single diagnostic rule (``WUPOL001``) covers every per-unit and cap
    violation: the validator's error message names the violated limit and
    its value, and the diagnostic anchors on the line of the
    ``## Work Units`` section heading.
    """
    diagnostics: list[Diagnostic] = []
    if artifact_type != "plan":
        return diagnostics
    raw = parsed_content.get("work_units")
    if not raw:
        return diagnostics

    try:
        parsed = parse_work_units_from_artifact(parsed_content)
    except WorkUnitsValidationError:
        # Structural checks already surfaced; do not double-report.
        return diagnostics
    if parsed is None:
        return diagnostics

    workspace_root = _workspace_root(workspace)
    pipeline = _load_policy_pipeline(workspace_root)
    if pipeline is None:
        return diagnostics

    section_line = _section_line(content, "Work Units") or 1

    try:
        validate_work_units_against_policy(parsed, pipeline, phase="development")
    except PolicyValidationError as exc:
        diagnostics.append(
            Diagnostic(
                section_line,
                "Work Units",
                "WUPOL001",
                str(exc),
            )
        )
    return diagnostics


def _section_line(content: str, section_name: str) -> int | None:
    """Return the 1-based line of the first ``## {section_name}`` heading, or None."""
    try:
        document, _ = parse_markdown_document(content, allow_nested_headings=False)
    except Exception:
        return None
    section = document.section(section_name)
    if section is None:
        return None
    return section.line


def _load_policy_pipeline(workspace_root: Path) -> PipelinePolicy | None:
    """Load the workspace's effective pipeline policy. Fail-open on I/O errors."""
    try:
        bundle = load_policy(workspace_root / ".agent")
    except Exception:
        return None
    return bundle.pipeline


def _current_context_diagnostics(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    artifact_type: str,
    parsed_content: dict[str, object],
    deps: ArtifactHandlerDeps | None,
) -> list[Diagnostic]:
    """Apply authenticated active-run evidence checks after structural parsing."""
    session_run_id = _session_run_id(session)
    if artifact_type == "design_verdict":
        return _design_verdict_context_diagnostics(
            session, workspace, parsed_content, session_run_id
        )
    if artifact_type == "development_result":
        return _development_result_context_diagnostics(
            session, workspace, parsed_content, session_run_id, deps
        )
    return []


def _design_verdict_context_diagnostics(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    content: dict[str, object],
    session_run_id: str | None,
) -> list[Diagnostic]:
    run_id = content.get("run_id")
    if not isinstance(run_id, str) or session_run_id is None or run_id != session_run_id:
        return [
            Diagnostic(
                1,
                "Capture Provenance",
                "DV009",
                "design verdict run_id must match the active session run",
            )
        ]
    tier = content.get("judgement_tier")
    if tier not in ("deterministic", "on-demand"):
        return [
            Diagnostic(
                1,
                "Capture Provenance",
                "DV010",
                "design verdict must declare judgement_tier as deterministic or on-demand",
            )
        ]
    verdict_id = content.get("verdict_id")
    if not isinstance(verdict_id, str) or not verdict_id:
        return [
            Diagnostic(
                1,
                "Capture Provenance",
                "DV011",
                "design verdict must declare a non-empty verdict_id for development-result proof",
            )
        ]
    handles = _capture_handles(content)
    if handles is None:
        return [
            Diagnostic(
                1,
                "Capture Provenance",
                "DV012",
                "design verdict requires non-empty before_handles and after_handles",
            )
        ]
    return _ledger_handle_diagnostics(session, workspace, session_run_id, handles, "DV013")


def _development_result_context_diagnostics(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    content: dict[str, object],
    session_run_id: str | None,
    deps: ArtifactHandlerDeps | None,
) -> list[Diagnostic]:
    session_diagnostics = development_result_session_diagnostics(session, workspace, content)
    if session_diagnostics:
        return session_diagnostics
    if content.get("status") != "completed":
        return []
    diagnostics: list[Diagnostic] = []
    for key in ("plan_items_proven", "analysis_items_addressed"):
        proofs = content.get(key)
        if not isinstance(proofs, list):
            continue
        for proof in proofs:
            if not isinstance(proof, dict):
                continue
            item_id = proof.get("plan_item") if key == "plan_items_proven" else ""
            if not isinstance(item_id, str) or not is_ui_plan_item(item_id):
                continue
            verdict_id = proof.get("verdict_id")
            handles_value = proof.get("capture_handles")
            if not isinstance(verdict_id, str) or not isinstance(handles_value, tuple):
                diagnostics.append(
                    Diagnostic(
                        1,
                        "Plan Items Proven",
                        "DEV011",
                        "completed UI proof requires verdict_id plus before/after ralph://media handles",
                    )
                )
                continue
            if session_run_id is None or not _submitted_active_verdict_matches(
                session, workspace, session_run_id, verdict_id, handles_value, deps
            ):
                diagnostics.append(
                    Diagnostic(
                        1,
                        "Plan Items Proven",
                        "DEV012",
                        "completed UI proof must cite an active-run submitted design verdict and its handles",
                    )
                )
                continue
            diagnostics.extend(
                _ledger_handle_diagnostics(
                    session, workspace, session_run_id, handles_value, "DEV013"
                )
            )
    return diagnostics


def _capture_handles(content: dict[str, object]) -> tuple[str, ...] | None:
    before = content.get("before_handles")
    after = content.get("after_handles")
    if not isinstance(before, tuple) or not isinstance(after, tuple) or not before or not after:
        return None
    return (*before, *after)


def _ledger_handle_diagnostics(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    run_id: str,
    handles: tuple[str, ...],
    rule_id: str,
) -> list[Diagnostic]:
    workspace_root = _workspace_root(workspace)
    secret = session.broker_secret
    return [
        Diagnostic(
            1,
            "Capture Provenance",
            rule_id,
            f"capture handle {handle!r} is not authenticated by the active run ledger",
        )
        for handle in handles
        if parse_media_uri(handle) is None
        or not _active_run_ledger_has_handle(workspace_root, run_id, secret, handle)
    ]


def _active_run_ledger_has_handle(
    workspace_root: Path,
    run_id: str,
    secret: str | None,
    handle: str,
) -> bool:
    candidates: tuple[tuple[str, dict[str, object]], ...] = (
        ("read_media", {"path": handle}),
        ("read_image", {"path": handle}),
        ("resources/read", {"uri": handle}),
    )
    for tool_name, params in candidates:
        if wire_evidence_for(
            workspace_root,
            run_id,
            tool_name=tool_name,
            secret=secret,
            params_digest=params_digest(params),
        ):
            return True
    return False


def _submitted_active_verdict_matches(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    run_id: str | None,
    verdict_id: str,
    handles: tuple[str, ...],
    deps: ArtifactHandlerDeps | None,
) -> bool:
    if run_id is None or not artifact_receipt_present(
        _workspace_root(workspace),
        run_id,
        "design_verdict",
        backend=(deps or DEFAULT_ARTIFACT_HANDLER_DEPS).backend,
        receipt_secret=session.broker_secret,
    ):
        return False
    artifact_path = _resolve_artifact_dir(session, workspace) / "design_verdict.md"
    backend = (deps or DEFAULT_ARTIFACT_HANDLER_DEPS).backend
    try:
        verdict_content = backend.read_text(artifact_path, encoding="utf-8")
    except (KeyError, OSError, UnicodeDecodeError):
        return False
    parsed, diagnostics = parse_and_validate(verdict_content, get_spec("design_verdict"))
    if any(diagnostic.severity == "error" for diagnostic in diagnostics):
        return False
    cited_handles = _capture_handles(parsed)
    return parsed.get("verdict_id") == verdict_id and cited_handles == handles


def _planning_finding_target_diagnostics(
    session: CoordinationSessionLike,
    workspace: WorkspaceLike,
    artifact_type: str,
    content: str,
    deps: ArtifactHandlerDeps | None,
) -> list[Diagnostic]:
    """Reject planning findings that cannot bind to the submitted plan's steps."""
    if artifact_type != "planning_analysis_decision":
        return []
    decision, _ = parse_markdown_document(content)
    target_lines = {
        item.identifier: item.line
        for section_name in ("What Came Up Short", "Criterion Verdicts")
        for section in decision.sections_named(section_name)
        for item in section.items
    }
    raw_targets = _parse_with_overrides(artifact_type, content)[0].get("finding_targets", {})
    if not isinstance(raw_targets, dict):
        return []
    targets = {
        finding_id: target
        for finding_id, target in raw_targets.items()
        if isinstance(finding_id, str) and isinstance(target, str)
    }
    backend = (deps or DEFAULT_ARTIFACT_HANDLER_DEPS).backend
    plan_path = _resolve_artifact_dir(session, workspace) / "plan.md"
    try:
        plan_content = backend.read_text(plan_path, encoding="utf-8")
    except (KeyError, OSError, UnicodeDecodeError):
        return [
            Diagnostic(
                target_lines.get(str(finding_id), 1),
                "What Came Up Short",
                "ANALYSIS004",
                "planning finding target cannot be validated because no submitted plan is available",
            )
            for finding_id in targets
        ]
    plan, _ = parse_markdown_document(plan_content)
    step_ids = step_number_map(plan, [])
    return [
        Diagnostic(
            target_lines.get(str(finding_id), 1),
            "What Came Up Short",
            "ANALYSIS004",
            f"planning finding references unknown step {target!r}; use an existing submitted plan step or 'Plan-level:'",
        )
        for finding_id, target in targets.items()
        if target != "plan-level" and target not in step_ids
    ]


__all__ = [
    "_active_run_ledger_has_handle",
    "_capture_handles",
    "_current_context_diagnostics",
    "_design_verdict_context_diagnostics",
    "_development_result_context_diagnostics",
    "_diagnostic_payload",
    "_ledger_handle_diagnostics",
    "_load_policy_pipeline",
    "_override_payload",
    "_parse_with_overrides",
    "_planning_finding_target_diagnostics",
    "_section_line",
    "_severity_counts",
    "_submitted_active_verdict_matches",
    "_work_units_policy_check",
]
