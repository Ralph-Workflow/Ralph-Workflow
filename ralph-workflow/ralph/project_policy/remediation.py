from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ralph.project_policy import markers, preflight, validators
from ralph.project_policy.pipeline_graph import PHASE_REMEDIATION
from ralph.prompts.template_engine import render_template
from ralph.prompts.template_registry import (
    load_partial_templates,
    packaged_template_root,
)

PROMPT_TEMPLATE_NAME: str = "policy_remediation.jinja"

if TYPE_CHECKING:
    from ralph.language_detector.models import ProjectStack
    from ralph.project_policy._readiness_assessment import ReadinessAssessment
    from ralph.project_policy.analysis import InvokePolicyAgent
    from ralph.project_policy.analysis_decision import AnalysisDecision
    from ralph.project_policy.models import PolicyFinding
    from ralph.workspace.protocol import Workspace

EmitFn = Callable[[str], None]


class RemediationInvocationError(RuntimeError):
    pass


def _noop_emit(message: str) -> None:
    pass


def _serialize_findings(findings: list[PolicyFinding]) -> str:
    items: list[str] = []
    for finding in findings:
        severity_label = "blocking" if finding.blocking else "warning"
        items.append(
            f"- requirement_id: {finding.requirement_id}\n"
            f"  path: {finding.path}\n"
            f"  severity: {severity_label}\n"
            f"  missing_evidence: {finding.missing_evidence}\n"
            f"  required_outcome: {finding.required_outcome}"
        )
    return "\n".join(items)


def _load_prompt_template() -> str:
    return (packaged_template_root() / PROMPT_TEMPLATE_NAME).read_text(encoding="utf-8")


def _render_prompt(
    findings: list[PolicyFinding],
    analysis_feedback: AnalysisDecision | None = None,
    *,
    workspace: Workspace | None = None,
    assessment: ReadinessAssessment | None = None,
) -> str:
    feedback_lines = analysis_feedback.feedback_lines() if analysis_feedback is not None else []
    if analysis_feedback is not None and analysis_feedback.summary:
        feedback_lines.insert(0, f"- summary: {analysis_feedback.summary}")
    if assessment is not None:
        sc = "true" if assessment.structure_complete else "false"
        iv = "true" if assessment.implementation_verified else "false"
        oer = "true" if assessment.outcome_evidence_reviewed else "false"
    else:
        sc = iv = oer = "unknown"
    variables = {
        "readiness_structure_complete": sc,
        "readiness_implementation_verified": iv,
        "readiness_outcome_evidence_reviewed": oer,
        "findings_block": _serialize_findings(findings),
        "analysis_feedback_block": "\n".join(feedback_lines),
        "analysis_feedback_status": (
            analysis_feedback.status if analysis_feedback is not None else ""
        ),
        "analysis_feedback_summary": (
            analysis_feedback.summary if analysis_feedback is not None else ""
        ),
        "canonical_dir": markers.CANONICAL_DIR,
        "gate_script_policy_path": f"{markers.CANONICAL_DIR}gate-script-policy.md",
        "approved_tools": ", ".join(sorted(markers.APPROVED_GATE_TOOLS)),
        "applicability_overrides_path": markers.APPLICABILITY_OVERRIDES_PATH,
        "migrated_marker": markers.MIGRATED_MARKER_TEMPLATE.format(target="<canonical-filename>"),
        "agents_block_begin": markers.AGENTS_BLOCK_BEGIN,
        "agents_block_end": markers.AGENTS_BLOCK_END,
        "LAST_RETRY_ERROR": (
            _read_retry_hint(workspace, PHASE_REMEDIATION) if workspace is not None else ""
        ),
    }
    partials = load_partial_templates((packaged_template_root(),))
    return render_template(_load_prompt_template(), variables, partials)


def _read_retry_hint(workspace: Workspace, phase: str) -> str:
    from ralph.phases.required_artifacts import retry_hint_path

    path = retry_hint_path(phase)
    if not workspace.exists(path):
        return ""
    try:
        return workspace.read(path)
    except Exception:
        return ""


def _write_prompt(workspace: Workspace, prompt_text: str) -> str:
    path = preflight.REMEDIATION_PROMPT_REL_PATH
    parent_dir = "/".join(path.split("/")[:-1])
    if parent_dir:
        workspace.mkdirs(parent_dir)
    workspace.write(path, prompt_text)
    return path


def run_remediation_phase(
    workspace: Workspace,
    stack: ProjectStack,
    findings: list[PolicyFinding],
    *,
    invoke_agent: InvokePolicyAgent,
    analysis_feedback: AnalysisDecision | None = None,
    emit: EmitFn = _noop_emit,
    assessment: ReadinessAssessment | None = None,
) -> list[PolicyFinding]:
    prompt_text = _render_prompt(
        findings, analysis_feedback, workspace=workspace, assessment=assessment
    )
    prompt_path = _write_prompt(workspace, prompt_text)
    emit(f"project-policy-readiness: invoking remediation agent ({len(findings)} open findings)")
    success = bool(invoke_agent(phase=PHASE_REMEDIATION, prompt_path=prompt_path))

    remaining = validators.validate_readiness(workspace, stack)
    if remaining and success:
        emit(
            f"project-policy-readiness: agent reported success but {len(remaining)} findings remain"
        )
    elif remaining:
        emit(
            f"project-policy-readiness: agent reported failure "
            f"({len(remaining)} findings remaining)"
        )
    return remaining


def render_blocked_report(findings: list[PolicyFinding]) -> list[str]:
    return _render_blocked_report(findings)


def _render_blocked_report(findings: list[PolicyFinding]) -> list[str]:
    return [
        "project-policy-readiness: NOT READY (proceeding anyway)",
        *(
            (
                f"  - {finding.requirement_id}  path={finding.path}\n"
                f"      missing: {finding.missing_evidence}\n"
                f"      fix:     {finding.required_outcome}"
            )
            for finding in findings
        ),
    ]


__all__ = [
    "PROMPT_TEMPLATE_NAME",
    "RemediationInvocationError",
    "render_blocked_report",
    "run_remediation_phase",
]
