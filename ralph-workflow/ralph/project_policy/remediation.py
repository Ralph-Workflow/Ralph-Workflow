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


def _render_prompt(findings: list[PolicyFinding]) -> str:
    """Build the remediation prompt text.

    The prompt is a deterministic, versionable task definition: the agent
    sees the exact findings list, the canonical directory, the migration
    contract, and the rules for completing the work.
    """
    findings_block = _serialize_findings(findings)
    approved_tools = ", ".join(sorted(markers.APPROVED_GATE_TOOLS))
    return f"""# Ralph Workflow Project-Policy Remediation

You are remediating a project so it passes the Ralph Workflow project-policy-readiness
deterministic validator. The validator is byte-exact and machine-checkable; near-miss
prose, extra whitespace, or case changes do not satisfy any requirement.

## Findings

{findings_block}

## Required actions

1. INSPECT the project: enumerate languages, frameworks, package managers, test
   frameworks, dependency manifests, and existing CONTRIBUTING / TESTING /
   DEVELOPMENT docs. Do not invent commands or tools.
2. COMPLETE every canonical file under {markers.CANONICAL_DIR}:
   * Replace every `RALPH-FACT:` line with a verified project fact.
   * Add at least one runnable `RALPH-COMMAND:` line for each verification
     gate (testing, typecheck, lint, dependency audit, verification), or an
     explicit `RALPH-INAPPLICABLE:` line with a reason.
   * Every `RALPH-COMMAND:` value MUST start with an approved gate tool —
     the validator checks the FIRST whitespace-separated token against a
     fixed allowlist and rejects everything else. Approved tools:
     {approved_tools}.
     Wrap any other tool in an approved runner (e.g. `make <target>`,
     `uv run <tool>`, `npx <tool>`).
   * Sections marked with a `REPLACE-ME` comment carry their own in-place
     instructions: follow the instruction, then delete the comment. The
     `REPLACE-ME` token is a validator placeholder, so readiness stays
     blocked while any such comment remains.
   * For typecheck and lint policies, declare every applicable language via
     `RALPH-LANG: <Language>` followed by a RALPH-COMMAND or RALPH-INAPPLICABLE.
   * Add the citation block under `## Research basis` with publisher, title,
     URL (http), and review date.
   * Remove inapplicable conditional sections instead of marking them
     complete; keep the required headings.
   * Where an existing project rule is STRICTER than the starter text,
     preserve the stricter rule — reconcile contradictions by adapting the
     stricter side, never by weakening the policy.
   * Delete the `RALPH-STARTER-TEMPLATE` banner comment at the top of each
     starter once the file holds verified project policy; the validator
     blocks readiness while the banner remains.
   * The finished file must read as durable policy: every sentence tells a
     future agent how to FOLLOW or ENFORCE the policy. Do not leave
     fill-in instructions, references to starters or placeholders, or
     validator mechanics in the prose. The required `Ralph markers`
     section is the one designated home for machine markers — keep it,
     and keep it minimal.
   * There is NO completion marker to add. A file is complete exactly when
     no `RALPH-STARTER-TEMPLATE` banner, no `REPLACE-ME` comment, and no
     placeholder token remains and every requirement above is satisfied —
     completion is the absence of unresolved markers.
3. MIGRATE existing project policy-like content into the matching canonical
   file and add `{markers.MIGRATED_MARKER_TEMPLATE.format(target="<canonical-filename>")}`
   at the old location (replacing `<canonical-filename>` with the destination).
   OR remove the recognized heading so the file is no longer a candidate.
   For standard community files (SECURITY.md, CONTRIBUTING.md, and similar
   ecosystem-convention locations) KEEP the file and its heading and use the
   migrated marker — their location and headings are conventions other tools
   and humans rely on.
4. CONFIGURE executable gates so every declared command actually runs in the
   environment. Document any command that cannot be run and the reason.
5. INTEGRATE the managed block naturally into AGENTS.md — never leave it as
   a bolted-on section appended after the user's content. The block markers
   `{markers.AGENTS_BLOCK_BEGIN}` and `{markers.AGENTS_BLOCK_END}` are
   invisible HTML comments, so you may RELOCATE the block to wherever it
   reads best in the document (near the top, or inside an existing
   quality/testing section) and rewrite its body to match the document's
   tone and structure. Requirements: exactly one block whose BODY
   references {markers.CANONICAL_DIR} so ANY AI agent reading AGENTS.md
   (Claude, Codex, Cursor, opencode, ...) can discover and follow the
   project policies; migrate policy-like sections of AGENTS.md itself into
   the matching canonical policy file (single source of truth) and resolve
   the migration finding; preserve user-authored non-policy content; keep
   AGENTS.md short — replace the bootstrap placeholder instructions with a
   concise pointer (a few lines), never leaving the long remediation
   instructions behind.
6. UPDATE CLAUDE.md (if present) so Claude-compatible agents see the AGENTS.md
   pointer (a default CLAUDE.md is created on the first preflight if missing).
7. RUN every declared verification command and report the outcome, including
   any command you could not run and the remaining risk.
8. REPORT changed files, migrated sources, adopted-or-adapted starter rules,
   research sources, commands run, and unresolved blockers.

## Hard rules

* Do NOT weaken, disable, or skip any deterministic check.
* Do NOT mark a policy complete while unresolved placeholders, missing
  per-language coverage, or unresolved migration findings remain.
* Do NOT add dependencies, abstractions, or numeric targets without
  demonstrated need from repository evidence.
* The canonical policies are LIVING DOCUMENTS: they must evolve with the
  project, so record what you learn (verified facts, commands, structure)
  in them rather than leaving stale boilerplate.
* Conflicts between starter boilerplate and the project's established
  practice are resolved in favor of the existing project policy — adapt
  the canonical file to the project, never the reverse. A looser project
  practice is NOT such a conflict: per the stricter-rule requirement
  above, the stronger requirement wins unless the project documents an
  explicit exception.
* Evolution MUST NOT subvert a policy's INTENT: never weaken, dilute, or
  delete a requirement so that a failing change passes.

After the agent returns, the deterministic validator is re-run. The agent's
own completion claim is never sufficient evidence; only the validator
result permits the workflow to continue.
"""


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
