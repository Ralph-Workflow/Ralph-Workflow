"""The policy-remediation PHASE: write and fix the canonical policy files.

This module owns ONE phase, not the loop. It invokes a remediation agent through
:func:`ralph.pipeline.effect_executor.execute_agent_effect` and ALWAYS re-runs
the deterministic validator afterward -- an agent's completion claim alone is
never sufficient evidence that the project is policy-ready. The loop, the
analysis phase, and the routing between them live in
:mod:`ralph.project_policy.pipeline_driver` and
:mod:`ralph.project_policy.pipeline_graph`.

The remediation phase runs OUT-OF-GRAPH: the shipped default pipeline
(``ralph/policy/defaults/pipeline.toml``) has no phase bound to the
``policy_remediation`` drain, because policy readiness is a startup preflight
concern rather than a step of the development pipeline.

Handoff contract (NON-ARTIFACT):

The findings are handed to the agent through a materialized prompt at
``.agent/tmp/policy_remediation_prompt.md`` written via the workspace seam.
This is a justified non-artifact handoff because:

* ``.agent/artifacts/<type>.json`` writes are forbidden and AST-audited by
  ``ralph/testing/audit_artifact_submission_canonical_path.py``.
* The remediation drain is deliberately DENIED ``artifact.submit`` in
  ``ralph/mcp/session_plan.py``: it is judged by the deterministic validator
  that re-runs after it exits, so it has no verdict of its own to submit.
  (Its ANALYSIS counterpart is granted ``artifact.submit`` -- returning a
  routing decision is that phase's entire purpose.)
* The prompt is the agent's task definition, not a structured artifact.
"""

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

#: Packaged Jinja template holding the remediation prompt body. Kept alongside
#: the pipeline prompt templates so remediation follows the same convention.
PROMPT_TEMPLATE_NAME: str = "policy_remediation.jinja"

if TYPE_CHECKING:
    from ralph.language_detector.models import ProjectStack
    from ralph.project_policy.analysis import InvokePolicyAgent
    from ralph.project_policy.analysis_decision import AnalysisDecision
    from ralph.project_policy.models import PolicyFinding
    from ralph.workspace.protocol import Workspace

EmitFn = Callable[[str], None]


class RemediationInvocationError(RuntimeError):
    """The remediation agent could not be launched at all.

    Distinct from an agent that ran and failed: a launch crash is deterministic
    infrastructure breakage, so retrying it burns the whole loop budget in
    milliseconds and floods the display. The driver stops looping on the first
    occurrence -- and then lets the RUN CONTINUE anyway. A broken agent
    subprocess is not a reason to refuse to do development work; see
    :mod:`ralph.project_policy.pipeline_driver`.
    """


def _noop_emit(message: str) -> None:
    """Default emit callback used when no display is injected."""


def _serialize_findings(findings: list[PolicyFinding]) -> str:
    """Render the findings into a stable, machine-readable block."""
    return "\n".join(
        (
            f"- requirement_id: {finding.requirement_id}\n"
            f"  path: {finding.path}\n"
            f"  missing_evidence: {finding.missing_evidence}\n"
            f"  required_outcome: {finding.required_outcome}"
        )
        for finding in findings
    )


def _load_prompt_template() -> str:
    """Read the packaged remediation prompt template body."""
    return (packaged_template_root() / PROMPT_TEMPLATE_NAME).read_text(encoding="utf-8")


def _render_prompt(
    findings: list[PolicyFinding],
    analysis_feedback: AnalysisDecision | None = None,
) -> str:
    """Build the remediation prompt text.

    The prompt is a deterministic, versionable task definition: the agent sees
    the exact findings list, the canonical directory, the migration contract, and
    the rules for completing the work. The body lives in the packaged Jinja
    template :data:`PROMPT_TEMPLATE_NAME` (consistent with the pipeline prompt
    templates); this function supplies the marker-derived variables it
    interpolates.

    ``analysis_feedback`` carries what the ANALYSIS phase said came up short on
    the previous iteration. It is what makes the loop converge rather than
    repeat: without it the remediation agent would re-derive the same policy from
    the same findings and produce the same output.
    """
    feedback_lines = (
        analysis_feedback.feedback_lines() if analysis_feedback is not None else []
    )
    variables = {
        "findings_block": _serialize_findings(findings),
        "analysis_feedback_block": "\n".join(feedback_lines),
        "analysis_feedback_summary": (
            analysis_feedback.summary if analysis_feedback is not None else ""
        ),
        "canonical_dir": markers.CANONICAL_DIR,
        "gate_script_policy_path": f"{markers.CANONICAL_DIR}gate-script-policy.md",
        "approved_tools": ", ".join(sorted(markers.APPROVED_GATE_TOOLS)),
        "applicability_overrides_path": markers.APPLICABILITY_OVERRIDES_PATH,
        "migrated_marker": markers.MIGRATED_MARKER_TEMPLATE.format(
            target="<canonical-filename>"
        ),
        "agents_block_begin": markers.AGENTS_BLOCK_BEGIN,
        "agents_block_end": markers.AGENTS_BLOCK_END,
    }
    # Load the packaged shared partials so `{% include 'shared/... %}` in the
    # template resolves, the same way the pipeline prompt templates do.
    partials = load_partial_templates((packaged_template_root(),))
    return render_template(_load_prompt_template(), variables, partials)


def _write_prompt(workspace: Workspace, prompt_text: str) -> str:
    """Materialize the remediation prompt under .agent/tmp via the workspace seam."""
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
) -> list[PolicyFinding]:
    """Run ONE remediation phase and return the REVALIDATED findings.

    Materializes the prompt (the open findings, plus whatever the analysis phase
    said came up short last round), invokes the agent chain, and then ALWAYS
    re-runs the deterministic validator. The agent's own success/failure report
    is deliberately NOT the return value: an agent that swears it fixed
    everything and an agent that gives up are treated identically, because the
    only evidence that ever counted is what the validator says afterward.

    Args:
        workspace: Injected workspace seam.
        stack: Detected project stack.
        findings: The findings currently open against the project.
        invoke_agent: Callable that runs the remediation agent chain with the
            materialized prompt. Injected so tests can substitute a fake drain.
        analysis_feedback: The previous analysis decision, when looping back.
        emit: Display callback.

    Returns:
        The findings still open AFTER the agent ran -- empty when the project now
        passes the deterministic validator.

    Raises:
        RemediationInvocationError: When the agent could not be launched at all.
            Propagated to the driver, which stops looping but still lets the run
            continue.
    """
    prompt_text = _render_prompt(findings, analysis_feedback)
    prompt_path = _write_prompt(workspace, prompt_text)
    emit(
        f"project-policy-readiness: invoking remediation agent "
        f"({len(findings)} open findings)"
    )
    success = bool(invoke_agent(phase=PHASE_REMEDIATION, prompt_path=prompt_path))

    # ALWAYS revalidate. Never trust the agent's claim alone.
    remaining = validators.validate_readiness(workspace, stack)
    if remaining and success:
        emit(
            f"project-policy-readiness: agent reported success but "
            f"{len(remaining)} findings remain"
        )
    elif remaining:
        emit(
            f"project-policy-readiness: agent reported failure "
            f"({len(remaining)} findings remaining)"
        )
    return remaining


def render_blocked_report(findings: list[PolicyFinding]) -> list[str]:
    """Render the report for a policy that could not be made ready.

    Each line carries the stable requirement id, path, missing evidence, and
    required outcome so the operator can address each item without re-running the
    validator -- and so the NEXT run picks up exactly where this one left off.
    """
    return _render_blocked_report(findings)


def _render_blocked_report(findings: list[PolicyFinding]) -> list[str]:
    """Render the not-ready report.

    Each line carries the stable requirement id, path, missing evidence,
    and required outcome so the operator can address each item without
    re-running the validator.
    """
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
