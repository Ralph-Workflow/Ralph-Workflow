
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ralph.phases.required_artifacts import clear_validation_retry_hint
from ralph.project_policy import analysis, evidence, markers, remediation, validators
from ralph.project_policy._readiness_assessment import ReadinessAssessment
from ralph.project_policy.models import PolicyFinding, ReadinessResult, ReadinessStatus
from ralph.project_policy.pipeline_graph import (
    DEFAULT_ANALYSIS_CAP,
    PHASE_ANALYSIS,
    PHASE_REMEDIATION,
    TERMINAL_DONE,
    analysis_budget_spent,
    phase_definition,
    resolve_decision,
)

if TYPE_CHECKING:
    from ralph.language_detector.models import ProjectStack
    from ralph.project_policy._command_outcome import CommandOutcome
    from ralph.project_policy.analysis import InvokePolicyAgent
    from ralph.project_policy.analysis_decision import AnalysisDecision
    from ralph.workspace.protocol import Workspace

EmitFn = Callable[[str], None]


def _noop_emit(message: str) -> None:
    pass


def _not_ready(
    findings: list[PolicyFinding],
    reason: str,
    command_outcomes: tuple[CommandOutcome, ...] = (),
) -> ReadinessResult:
    return ReadinessResult(
        status=ReadinessStatus.BLOCKED,
        findings=findings,
        command_outcomes=command_outcomes,
        assessment=ReadinessAssessment(structure_complete=not any(f.blocking for f in findings)),
        report_lines=[reason, *remediation.render_blocked_report(findings)],
    )


AttemptCallback = Callable[[int], None]


def _noop_attempt_callback(attempt: int) -> None:
    pass


def run_policy_pipeline(
    workspace: Workspace,
    stack: ProjectStack,
    findings: list[PolicyFinding],
    *,
    invoke_agent: InvokePolicyAgent,
    entry_phase: str = PHASE_REMEDIATION,
    analysis_cap: int = DEFAULT_ANALYSIS_CAP,
    emit: EmitFn = _noop_emit,
    on_remediation_attempt: AttemptCallback = _noop_attempt_callback,
) -> ReadinessResult:
    phase = entry_phase
    iteration = 0
    current = list(findings)
    feedback: AnalysisDecision | None = None
    blessed = False

    try:
        while phase != TERMINAL_DONE:
            if phase == PHASE_REMEDIATION:
                on_remediation_attempt(iteration + 1)
                current_assessment = _assess(
                    workspace, stack, current, _collect_command_outcomes(workspace)
                )
                current = remediation.run_remediation_phase(
                    workspace,
                    stack,
                    current,
                    invoke_agent=invoke_agent,
                    analysis_feedback=feedback,
                    emit=emit,
                    assessment=current_assessment,
                )
                feedback = None
                phase = _route_after_remediation(current, iteration, analysis_cap, emit)
                if phase == PHASE_REMEDIATION:
                    iteration += 1
                continue

            if analysis_budget_spent(iteration, analysis_cap):
                phase = TERMINAL_DONE
                continue

            if any(f.blocking for f in current):
                iteration += 1
                phase = PHASE_REMEDIATION
                continue

            current_assessment = _assess(
                workspace, stack, current, _collect_command_outcomes(workspace)
            )
            decision = analysis.run_analysis_phase(
                workspace,
                invoke_agent=invoke_agent,
                emit=emit,
                assessment=current_assessment,
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


def _combined_policy_content_hash(
    workspace: Workspace,
    stack: ProjectStack,
) -> str:
    return evidence.evidence_signature(workspace, stack)


def _declared_gate_commands(workspace: Workspace) -> tuple[str, ...]:
    commands: list[str] = []
    for path in (f"{markers.CANONICAL_DIR}{name}" for name in markers.CORE_POLICY_FILES):
        if not workspace.exists(path):
            continue
        commands.extend(
            line[len(markers.COMMAND_MARKER) :].strip()
            for line in workspace.read(path).splitlines()
            if line.startswith(markers.COMMAND_MARKER)
        )
    return tuple(commands)


def _assess(
    workspace: Workspace,
    stack: ProjectStack,
    findings: list[PolicyFinding],
    outcomes: tuple[CommandOutcome, ...],
) -> ReadinessAssessment:
    structure_complete = not any(f.blocking for f in findings)
    implementation_verified = False
    if outcomes:
        expected_hash = _combined_policy_content_hash(workspace, stack)
        implementation_verified = all(
            outcome.exit_code == 0 and outcome.policy_content_hash == expected_hash
            for outcome in outcomes
        )
    outcome_evidence_reviewed = False
    if implementation_verified:
        recorded = {outcome.command for outcome in outcomes}
        outcome_evidence_reviewed = all(
            command in recorded for command in _declared_gate_commands(workspace)
        )
    return ReadinessAssessment(
        structure_complete=structure_complete,
        implementation_verified=implementation_verified,
        outcome_evidence_reviewed=outcome_evidence_reviewed,
    )


def _collect_command_outcomes(workspace: Workspace) -> tuple[CommandOutcome, ...]:
    raw_artifact = analysis.read_execution_outcomes(workspace)
    if raw_artifact:
        return raw_artifact
    return ()


def _route_after_remediation(
    current: list[PolicyFinding],
    iteration: int,
    analysis_cap: int,
    emit: EmitFn,
) -> str:
    if analysis_budget_spent(iteration, analysis_cap):
        emit(
            f"project-policy-readiness: analysis cap reached "
            f"({analysis_cap}), skipping further review"
        )
        return TERMINAL_DONE
    if any(f.blocking for f in current):
        return PHASE_REMEDIATION
    on_success = phase_definition(PHASE_REMEDIATION).on_success
    if on_success is None:
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
    command_outcomes = _collect_command_outcomes(workspace)
    if not blessed:
        emit(
            "project-policy-readiness: could not reach a ready policy; "
            "continuing to the development run anyway"
        )
        return _not_ready(
            current,
            "project-policy-readiness: analysis budget exhausted",
            command_outcomes=command_outcomes,
        )

    remaining = validators.validate_readiness(workspace, stack)
    blocking_remaining = [f for f in remaining if f.blocking]
    if blocking_remaining:
        return ReadinessResult(
            status=ReadinessStatus.BLOCKED,
            findings=remaining,
            command_outcomes=command_outcomes,
            assessment=_assess(workspace, stack, remaining, command_outcomes),
            report_lines=[
                "project-policy-readiness: analysis approved but the validator still reports findings",
                *remediation.render_blocked_report(remaining),
            ],
        )

    clear_validation_retry_hint(workspace, PHASE_REMEDIATION)
    clear_validation_retry_hint(workspace, PHASE_ANALYSIS)
    emit("project-policy-readiness: ready (validator clean, analysis approved)")
    return ReadinessResult(
        status=ReadinessStatus.READY,
        findings=remaining,
        command_outcomes=command_outcomes,
        assessment=_assess(workspace, stack, [], command_outcomes),
        report_lines=["project-policy-readiness: ready"],
    )


__all__ = ["run_policy_pipeline"]
