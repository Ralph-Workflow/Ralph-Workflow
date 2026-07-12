"""Synchronous, out-of-graph policy-remediation driver.

The shipped default pipeline (``ralph/policy/defaults/pipeline.toml``) has
NO review or fix drain, and ``ralph/phases/review.py`` is dormant. The
prior plan to inject an existing review drain is therefore unimplementable.
This driver runs remediation OUT-OF-GRAPH: it invokes one remediation agent
through :func:`ralph.pipeline.effect_executor.execute_agent_effect` (which
is self-managed, self-contained, and returns a ``PipelineEvent``) and
ALWAYS re-runs the deterministic validator afterward. An agent's completion
claim alone is never sufficient evidence that the project is policy-ready.

Handoff contract (NON-ARTIFACT):

The findings are handed to the agent through a materialized prompt at
``.agent/tmp/policy_remediation_prompt.md`` written via the workspace seam.
This is a justified non-artifact handoff because:

* ``.agent/artifacts/<type>.json`` writes are forbidden and AST-audited by
  ``ralph/testing/audit_artifact_submission_canonical_path.py``.
* No artifact backend is available at startup.
* The prompt is the agent's task definition, not a structured artifact.

The agent may still submit structured artifacts through the normal MCP
path during its run; this driver never depends on that.

Remediation is BUDGET-BOUNDED. ``max_attempts`` caps the loop. On budget
exhaustion the driver returns a :class:`ReadinessResult` with
:attr:`ReadinessStatus.BLOCKED` and the still-open findings preserved with
stable id/path/evidence/outcome so a future retry can pick up where the
driver left off.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from ralph.project_policy import cache, markers, preflight, validators
from ralph.project_policy.models import (
    PolicyFinding,
    ReadinessResult,
    ReadinessStatus,
)
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
    from ralph.workspace.protocol import Workspace

# Default budget for synchronous remediation attempts.
DEFAULT_MAX_ATTEMPTS: int = 2

EmitFn = Callable[[str], None]


class RemediationInvocationError(RuntimeError):
    """The remediation agent could not be launched at all.

    Distinct from an agent that ran and failed: a launch crash is
    deterministic infrastructure breakage, so retrying it burns the whole
    attempt budget in milliseconds and floods the display. The driver
    aborts the loop on the first occurrence instead.
    """


class _InvokeRemediationAgent(Protocol):
    """Callable contract for invoking one remediation agent.

    The production implementation (supplied by ``ralph.cli.commands.run``)
    constructs an ``InvokeAgentEffect`` for the ``policy_remediation`` chain
    and calls ``ralph.pipeline.effect_executor.execute_agent_effect``.
    Returns True when the agent reported success, False otherwise.
    """

    def __call__(self, prompt_path: str) -> bool: ...


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


def _render_prompt(findings: list[PolicyFinding]) -> str:
    """Build the remediation prompt text.

    The prompt is a deterministic, versionable task definition: the agent
    sees the exact findings list, the canonical directory, the migration
    contract, and the rules for completing the work. The body lives in the
    packaged Jinja template :data:`PROMPT_TEMPLATE_NAME` (consistent with the
    pipeline prompt templates); this function supplies the marker-derived
    variables it interpolates.
    """
    variables = {
        "findings_block": _serialize_findings(findings),
        "canonical_dir": markers.CANONICAL_DIR,
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


def remediate(
    workspace: Workspace,
    stack: ProjectStack,
    findings: list[PolicyFinding],
    *,
    invoke_remediation_agent: _InvokeRemediationAgent,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    emit: EmitFn = _noop_emit,
) -> ReadinessResult:
    """Drive one bounded remediation loop and revalidate after every attempt.

    Args:
        workspace: Injected workspace seam.
        stack: Detected project stack.
        findings: Initial findings from the orchestrator (non-empty).
        invoke_remediation_agent: Callable that runs one remediation agent
            with the materialized prompt and returns True on success.
            Injected so tests can substitute a fake drain.
        max_attempts: Maximum number of (agent, revalidate) cycles.
        emit: Display callback. One short line per attempt.

    Returns:
        * :attr:`ReadinessStatus.READY` when revalidation passes; the cache
          is updated to the current evidence signature.
        * :attr:`ReadinessStatus.BLOCKED` when ``max_attempts`` is
          exhausted with findings still open. The remaining findings are
          preserved with stable id/path/evidence/outcome.
    """
    if max_attempts <= 0:
        return ReadinessResult(
            status=ReadinessStatus.BLOCKED,
            findings=findings,
            report_lines=[
                f"project-policy-readiness: blocked (max_attempts={max_attempts})",
            ],
        )

    current_findings = list(findings)
    for attempt_index in range(1, max_attempts + 1):
        prompt_text = _render_prompt(current_findings)
        prompt_path = _write_prompt(workspace, prompt_text)
        emit(
            f"project-policy-readiness: invoking remediation agent "
            f"(attempt {attempt_index}/{max_attempts}, "
            f"{len(current_findings)} open findings)"
        )
        try:
            success = bool(invoke_remediation_agent(prompt_path))
        except RemediationInvocationError as exc:
            emit(
                f"project-policy-readiness: remediation agent could not be "
                f"launched ({exc}); aborting remediation"
            )
            return ReadinessResult(
                status=ReadinessStatus.BLOCKED,
                findings=current_findings,
                report_lines=[
                    "project-policy-readiness: blocked "
                    f"(remediation agent could not be launched: {exc})",
                ],
            )
        # ALWAYS revalidate. Never trust the agent's claim alone.
        current_findings = validators.validate_readiness(workspace, stack)
        if not current_findings:
            cache.write_cache(workspace, stack, ReadinessStatus.READY)
            emit("project-policy-readiness: ready (post-remediation revalidation passed)")
            return ReadinessResult(
                status=ReadinessStatus.READY,
                report_lines=[
                    "project-policy-readiness: ready (post-remediation)",
                ],
            )
        if not success:
            emit(
                f"project-policy-readiness: agent reported failure "
                f"({len(current_findings)} findings remaining)"
            )
        else:
            emit(
                f"project-policy-readiness: agent reported success but "
                f"{len(current_findings)} findings remain"
            )

    return ReadinessResult(
        status=ReadinessStatus.BLOCKED,
        findings=current_findings,
        report_lines=_render_blocked_report(current_findings),
    )


def _render_blocked_report(findings: list[PolicyFinding]) -> list[str]:
    """Render the BLOCKED-state report.

    Each line carries the stable requirement id, path, missing evidence,
    and required outcome so the operator can address each item without
    re-running the validator.
    """
    return [
        "project-policy-readiness: blocked",
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
    "DEFAULT_MAX_ATTEMPTS",
    "RemediationInvocationError",
    "remediate",
]
