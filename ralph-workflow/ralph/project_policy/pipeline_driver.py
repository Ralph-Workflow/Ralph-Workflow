"""Execute the hardcoded policy pipeline.

Drives the two-phase graph defined in :mod:`ralph.project_policy.pipeline_graph`:
remediation writes the policy, the deterministic validator gates it, analysis
reviews it, and the decision routes back to remediation or forward to done.

THE INVARIANT THIS MODULE EXISTS TO PROTECT
-------------------------------------------

**Policy readiness NEVER blocks the development run.** There is no code path in
this module that aborts a run. Whatever happens -- the analysis budget runs out
with findings still open, the agent chain has no configured agent, the agent
subprocess cannot even be launched -- the driver reports it, declines to cache a
false READY, and returns. The caller proceeds to planning.

That is a deliberate inversion of the previous behavior, where a BLOCKED policy
returned exit code 2 and killed the run. Policy is documentation about the
project; a project with imperfect documentation is still a project you can do
work on. Coupling the two meant a stale ``RALPH-LANG`` block for a language
nobody uses could stop all development. Never again. The only place a non-zero
exit survives is the ``--redo-policy-only`` / ``--run-policy-agents-only`` flags,
which have no development run to proceed to, so their exit code is the only
signal they can give.

The loop, with the default cap of 3::

    R  A  R  A  R  A  R  ->  done  ->  planning
    ^                    ^
    entry                the analysis budget is spent, so the driver runs one
                         FINAL remediation (applying the last review's feedback)
                         and then walks forward. It does not fail.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ralph.project_policy import analysis, cache, remediation, validators
from ralph.project_policy.models import PolicyFinding, ReadinessResult, ReadinessStatus
from ralph.project_policy.pipeline_graph import (
    DEFAULT_ANALYSIS_CAP,
    PHASE_REMEDIATION,
    TERMINAL_DONE,
    analysis_budget_spent,
    phase_definition,
    resolve_decision,
)

if TYPE_CHECKING:
    from ralph.language_detector.models import ProjectStack
    from ralph.project_policy.analysis import InvokePolicyAgent
    from ralph.project_policy.analysis_decision import AnalysisDecision
    from ralph.workspace.protocol import Workspace

EmitFn = Callable[[str], None]


def _noop_emit(message: str) -> None:
    """Default emit callback used when no display is injected."""


def _not_ready(findings: list[PolicyFinding], reason: str) -> ReadinessResult:
    """Build the result for a policy that could not be made ready.

    The status is BLOCKED, but BLOCKED does NOT mean "abort the run" -- it means
    "this policy is not ready". The caller maps it to a warning and proceeds. The
    still-open findings keep their stable ids so the next run resumes from here,
    and the READY cache is deliberately NOT written, so the next run retries
    rather than assuming success.
    """
    return ReadinessResult(
        status=ReadinessStatus.BLOCKED,
        findings=findings,
        report_lines=[reason, *remediation.render_blocked_report(findings)],
    )


def run_policy_pipeline(
    workspace: Workspace,
    stack: ProjectStack,
    findings: list[PolicyFinding],
    *,
    invoke_agent: InvokePolicyAgent,
    entry_phase: str = PHASE_REMEDIATION,
    analysis_cap: int = DEFAULT_ANALYSIS_CAP,
    emit: EmitFn = _noop_emit,
) -> ReadinessResult:
    """Run the policy pipeline to a terminal state. Never blocks the run.

    Args:
        workspace: Injected workspace seam.
        stack: Detected project stack.
        findings: The findings currently open against the project. May be empty
            when ``entry_phase`` is the analysis phase (``--run-policy-agents``
            audits an already-valid policy).
        invoke_agent: Callable that runs one phase's agent chain. Injected so
            tests can substitute a fake drain.
        entry_phase: Where to enter the graph. ``policy_remediation`` for the
            normal path; ``policy_remediation_analysis`` for
            ``--run-policy-agents``, which reviews the EXISTING policy in place
            and only rewrites it if analysis routes ``request_changes``.
        analysis_cap: The analysis loop budget.
        emit: Display callback.

    Returns:
        READY when the deterministic validator passes AND the analysis agent
        returned ``completed``; the READY cache is written. BLOCKED otherwise --
        which the caller reports and then proceeds past.
    """
    phase = entry_phase
    iteration = 0
    current = list(findings)
    feedback: AnalysisDecision | None = None
    blessed = False

    # A launch crash in EITHER phase is broken infrastructure, not a policy
    # shortfall: retrying it burns the whole budget in milliseconds. Stop looping
    # -- and still do NOT block the run.
    try:
        while phase != TERMINAL_DONE:
            if phase == PHASE_REMEDIATION:
                current = remediation.run_remediation_phase(
                    workspace,
                    stack,
                    current,
                    invoke_agent=invoke_agent,
                    analysis_feedback=feedback,
                    emit=emit,
                )
                feedback = None
                phase = _route_after_remediation(current, iteration, analysis_cap, emit)
                if phase == PHASE_REMEDIATION:
                    iteration += 1
                continue

            # PHASE_ANALYSIS.
            if analysis_budget_spent(iteration, analysis_cap):
                # Reachable only on an analysis ENTRY with a spent budget (a cap
                # of 0). The bypass applies here too: walk forward, never fail.
                phase = TERMINAL_DONE
                continue

            if current:
                # HARD GATE. The deterministic validator is still failing, so the
                # analysis agent is never consulted: reviewing the QUALITY of a
                # structurally invalid policy is wasted work, and an analysis
                # agent must never be in a position where its 'completed' could
                # launder a failing validator into a pass.
                iteration += 1
                phase = PHASE_REMEDIATION
                continue

            decision = analysis.run_analysis_phase(
                workspace, invoke_agent=invoke_agent, emit=emit
            )
            route = resolve_decision(decision.status)
            if route.reset_loop:
                iteration = 0
                blessed = True
            else:
                feedback = decision
                iteration += 1
            phase = route.target
    except remediation.RemediationInvocationError as exc:
        emit(
            f"project-policy-readiness: policy agent could not be launched "
            f"({exc}); continuing without a ready policy"
        )
        return _not_ready(
            current,
            f"project-policy-readiness: agent could not be launched ({exc})",
        )

    return _finish(workspace, stack, current, blessed=blessed, emit=emit)


def _route_after_remediation(
    current: list[PolicyFinding],
    iteration: int,
    analysis_cap: int,
    emit: EmitFn,
) -> str:
    """Return the phase to enter after a remediation phase completes.

    Three outcomes, in priority order:

    #. The analysis budget is spent -> the exhausted-analysis bypass fires. This
       remediation was the FINAL one; walk forward to the terminal phase. Note
       this is checked BEFORE the findings check, which is what guarantees the
       loop always terminates and always ends on a remediation.
    #. Findings remain -> loop back to remediation. Analysis is skipped (the hard
       gate); a structurally invalid policy is not worth an AI review.
    #. Otherwise -> the analysis phase, to review what was written.
    """
    if analysis_budget_spent(iteration, analysis_cap):
        emit(
            f"project-policy-readiness: analysis cap reached "
            f"({analysis_cap}), skipping further review"
        )
        return TERMINAL_DONE
    if current:
        return PHASE_REMEDIATION
    on_success = phase_definition(PHASE_REMEDIATION).on_success
    if on_success is None:  # pragma: no cover - the graph always defines it
        return TERMINAL_DONE
    return on_success


def _finish(
    workspace: Workspace,
    stack: ProjectStack,
    current: list[PolicyFinding],
    *,
    blessed: bool,
    emit: EmitFn,
) -> ReadinessResult:
    """Map the terminal state onto a :class:`ReadinessResult`.

    READY requires BOTH halves of the gate: the analysis agent said ``completed``
    AND the deterministic validator agrees. The validator is re-run one last time
    rather than trusting ``current``: the analysis drain holds
    ``process.exec_bounded``, so in principle a command it ran could have touched
    the workspace after the last validation. Cheap insurance on the one decision
    that gets cached.
    """
    if not blessed:
        emit(
            "project-policy-readiness: could not reach a ready policy; "
            "continuing to the development run anyway"
        )
        return _not_ready(
            current,
            "project-policy-readiness: analysis budget exhausted",
        )

    remaining = validators.validate_readiness(workspace, stack)
    if remaining:
        return _not_ready(
            remaining,
            "project-policy-readiness: analysis approved but the validator "
            "still reports findings",
        )

    cache.write_cache(workspace, stack, ReadinessStatus.READY)
    emit("project-policy-readiness: ready (validator clean, analysis approved)")
    return ReadinessResult(
        status=ReadinessStatus.READY,
        report_lines=["project-policy-readiness: ready"],
    )


__all__ = ["run_policy_pipeline"]
