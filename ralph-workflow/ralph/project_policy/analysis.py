from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, cast

from ralph.mcp.tools.names import (
    DECLARE_COMPLETE_TOOL,
    SUBMIT_MD_ARTIFACT_TOOL,
    VERIFY_MD_ARTIFACT_TOOL,
    RalphToolName,
    claude_tool_name,
    opencode_tool_name,
)
from ralph.phases.artifacts import (
    PhaseArtifactError,
    load_phase_artifact,
    unwrap_phase_artifact_content,
)
from ralph.phases.required_artifacts import retry_hint_path
from ralph.project_policy import markers
from ralph.project_policy._assessment import (
    extract_evidence_section,
    parse_execution_evidence,
)
from ralph.project_policy._assessment_error import EvidenceParseError
from ralph.project_policy.analysis_decision import AnalysisDecision
from ralph.project_policy.pipeline_graph import (
    DECISION_FAILED,
    PHASE_ANALYSIS,
    phase_definition,
)
from ralph.prompts.template_engine import render_template
from ralph.prompts.template_registry import (
    load_partial_templates,
    packaged_template_root,
)

if TYPE_CHECKING:
    from ralph.project_policy._command_outcome import CommandOutcome
    from ralph.project_policy._readiness_assessment import ReadinessAssessment
    from ralph.workspace.protocol import Workspace

PROMPT_TEMPLATE_NAME: str = "policy_remediation_analysis.jinja"

ANALYSIS_PROMPT_REL_PATH: str = ".agent/tmp/policy_remediation_analysis_prompt.md"

ANALYSIS_ARTIFACT_TYPE: str = "policy_remediation_analysis_decision"
ANALYSIS_ARTIFACT_REL_PATH: str = f".agent/artifacts/{ANALYSIS_ARTIFACT_TYPE}.md"

EmitFn = Callable[[str], None]


class InvokePolicyAgent(Protocol):
    def __call__(self, *, phase: str, prompt_path: str) -> bool: ...


def _noop_emit(message: str) -> None:
    pass


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    items = cast("list[object]", value)
    return [text for item in items if (text := str(item).strip())]


def _transport_tool_names(tool_name: RalphToolName) -> str:
    return (
        f"`{claude_tool_name(tool_name)}` for Claude, Claude Interactive, Codex, or Cursor; "
        f"`{opencode_tool_name(tool_name)}` for OpenCode; "
        f"`{tool_name}` for AGY, Pi, Nanocoder, or generic transports"
    )


def _render_prompt(
    workspace: Workspace,
    assessment: ReadinessAssessment | None = None,
) -> str:
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
        "canonical_dir": markers.CANONICAL_DIR,
        "portfolio_path": markers.PORTFOLIO_PATH,
        "gate_script_policy_path": f"{markers.CANONICAL_DIR}gate-script-policy.md",
        "artifact_type": ANALYSIS_ARTIFACT_TYPE,
        "policy_files": ", ".join(markers.CORE_POLICY_FILES),
        "approved_tools": ", ".join(sorted(markers.APPROVED_GATE_TOOLS)),
        "declare_complete_tool_names": _transport_tool_names(DECLARE_COMPLETE_TOOL),
        "submit_tool_names": _transport_tool_names(SUBMIT_MD_ARTIFACT_TOOL),
        "verify_tool_names": _transport_tool_names(VERIFY_MD_ARTIFACT_TOOL),
        "LAST_RETRY_ERROR": _read_retry_hint(workspace, PHASE_ANALYSIS),
    }
    partials = load_partial_templates((packaged_template_root(),))
    template = (packaged_template_root() / PROMPT_TEMPLATE_NAME).read_text(encoding="utf-8")
    return render_template(template, variables, partials)


def _read_retry_hint(workspace: Workspace, phase: str) -> str:
    path = retry_hint_path(phase)
    if not workspace.exists(path):
        return ""
    try:
        return workspace.read(path)
    except Exception:
        return ""


def _write_prompt(workspace: Workspace, prompt_text: str) -> str:
    parent_dir = "/".join(ANALYSIS_PROMPT_REL_PATH.split("/")[:-1])
    if parent_dir:
        workspace.mkdirs(parent_dir)
    workspace.write(ANALYSIS_PROMPT_REL_PATH, prompt_text)
    return ANALYSIS_PROMPT_REL_PATH


def clear_stale_decision(workspace: Workspace) -> None:
    if workspace.exists(ANALYSIS_ARTIFACT_REL_PATH):
        workspace.delete(ANALYSIS_ARTIFACT_REL_PATH)


def read_analysis_decision(workspace: Workspace) -> AnalysisDecision:
    if not workspace.exists(ANALYSIS_ARTIFACT_REL_PATH):
        return AnalysisDecision(
            status=DECISION_FAILED,
            summary="analysis agent submitted no decision artifact",
        )
    try:
        artifact = load_phase_artifact(workspace, ANALYSIS_ARTIFACT_REL_PATH)
        content = unwrap_phase_artifact_content(artifact, expected_type=ANALYSIS_ARTIFACT_TYPE)
    except PhaseArtifactError as exc:
        return AnalysisDecision(
            status=DECISION_FAILED,
            summary=f"analysis decision artifact is unusable: {exc}",
        )

    status = str(content.get("status", "")).strip().lower()
    decisions = _analysis_vocabulary()
    if status not in decisions:
        return AnalysisDecision(
            status=DECISION_FAILED,
            summary=(
                f"analysis agent returned an unrecognized decision {status!r}; "
                "treating it as failed"
            ),
        )
    return AnalysisDecision(
        status=status,
        summary=str(content.get("summary", "")).strip(),
        what_came_up_short=_string_list(content.get("what_came_up_short")),
        how_to_fix=_string_list(content.get("how_to_fix")),
    )


def _analysis_vocabulary() -> frozenset[str]:
    return frozenset(phase_definition(PHASE_ANALYSIS).decisions)


def run_analysis_phase(
    workspace: Workspace,
    *,
    invoke_agent: InvokePolicyAgent,
    emit: EmitFn = _noop_emit,
    assessment: ReadinessAssessment | None = None,
) -> AnalysisDecision:
    clear_stale_decision(workspace)
    prompt_path = _write_prompt(workspace, _render_prompt(workspace, assessment=assessment))

    success = bool(invoke_agent(phase=PHASE_ANALYSIS, prompt_path=prompt_path))
    decision = read_analysis_decision(workspace)
    if not success and decision.is_completed():
        return AnalysisDecision(
            status=DECISION_FAILED,
            summary=(
                "analysis agent chain failed after submitting a completed "
                "decision; discarding the decision"
            ),
        )
    emit(f"project-policy-readiness: analysis decision: {decision.status}")
    return decision


def read_execution_outcomes(workspace: Workspace) -> tuple[CommandOutcome, ...]:
    if not workspace.exists(ANALYSIS_ARTIFACT_REL_PATH):
        return ()
    raw = workspace.read(ANALYSIS_ARTIFACT_REL_PATH)
    section = extract_evidence_section(raw)
    if not section:
        return ()
    try:
        return parse_execution_evidence(section)
    except EvidenceParseError:
        return ()


__all__ = [
    "ANALYSIS_ARTIFACT_REL_PATH",
    "ANALYSIS_ARTIFACT_TYPE",
    "ANALYSIS_PROMPT_REL_PATH",
    "AnalysisDecision",
    "InvokePolicyAgent",
    "clear_stale_decision",
    "read_analysis_decision",
    "read_execution_outcomes",
    "run_analysis_phase",
]
