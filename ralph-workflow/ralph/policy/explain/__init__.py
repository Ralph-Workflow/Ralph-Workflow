"""Policy explanation — converts a PolicyBundle into a structured human-readable description.

This package answers the key questions a user has when looking at an unfamiliar policy:
- What happens after this phase succeeds?
- What makes a phase terminal?
- When is a commit required?
- When does the system retry vs fall back vs fail?
- When is parallel execution allowed?
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .budget_counter_explanation import BudgetCounterExplanation
from .commit_policy_explanation import CommitPolicyExplanation
from .lifecycle_explanation import LifecycleExplanation
from .loop_counter_explanation import LoopCounterExplanation
from .loop_policy_explanation import LoopPolicyExplanation
from .parallel_explanation import ParallelExplanation
from .phase_explanation import PhaseExplanation
from .policy_explanation import PolicyExplanation
from .post_commit_route_explanation import PostCommitRouteExplanation
from .recovery_explanation import RecoveryExplanation
from .terminal_outcome_explanation import TerminalOutcomeExplanation
from .verification_explanation import VerificationExplanation

if TYPE_CHECKING:
    from ralph.policy.models import AgentsPolicy, PhaseDefinition, PipelinePolicy, PolicyBundle


def explain_routing_decision(
    phase: str,
    target: str,
    reason: str,
    value: str,
    *,
    recovery: bool = False,
) -> str:
    """Build a human-readable routing explanation message."""
    if recovery:
        return (
            f"policy: '{phase}' routed to '{target}' because recovery was triggered "
            f"({reason}: {value})"
        )
    return f"policy: '{phase}' routed to '{target}' because the configured {reason} was '{value}'"


def _explain_phase(
    phase_name: str,
    phase_def: PhaseDefinition,
    pipeline: PipelinePolicy,
    agents: AgentsPolicy,
) -> PhaseExplanation:
    drain_name = phase_def.drain
    drain_config = agents.agent_drains.get(drain_name)
    chain_name = drain_config.chain if drain_config else None
    chain_config = agents.agent_chains.get(chain_name) if chain_name else None
    effective_retry = pipeline.effective_retry_policy(phase_name)

    loop_expl: LoopPolicyExplanation | None = None
    if phase_def.loop_policy is not None:
        lp = phase_def.loop_policy
        loop_expl = LoopPolicyExplanation(
            max_iterations=pipeline.loop_counters[lp.iteration_state_field].default_max,
            iteration_state_field=lp.iteration_state_field,
            loopback_review_outcome=lp.loopback_review_outcome,
        )

    commit_expl: CommitPolicyExplanation | None = None
    if phase_def.commit_policy is not None:
        cp = phase_def.commit_policy
        commit_expl = CommitPolicyExplanation(
            increments_counter=cp.increments_counter,
            loop_resets=list(cp.loop_resets),
            requires_artifact=cp.requires_artifact,
        )

    verification_expl: VerificationExplanation | None = None
    if phase_def.verification is not None:
        v = phase_def.verification
        verification_expl = VerificationExplanation(
            kind=v.kind,
            gate_for=v.gate_for,
            on_failure_route=v.on_failure_route,
        )

    workflow_fallback_info: tuple[str, str | None] | None = None
    if phase_def.workflow_fallback is not None:
        workflow_fallback_info = (
            phase_def.workflow_fallback.target,
            phase_def.workflow_fallback.note,
        )

    return PhaseExplanation(
        name=phase_name,
        role=phase_def.role,
        drain=drain_name,
        chain=chain_name,
        agents=list(chain_config.agents) if chain_config else [],
        max_retries=effective_retry.max_retries,
        skip_invocation=phase_def.skip_invocation,
        on_success=phase_def.transitions.on_success,
        on_failure=phase_def.transitions.on_failure,
        on_loopback=phase_def.transitions.on_loopback,
        bypass_routes=dict(phase_def.bypass_routes),
        decisions={dk: dr.target for dk, dr in phase_def.decisions.items()},
        loop_policy=loop_expl,
        commit_policy=commit_expl,
        terminal_outcome=phase_def.terminal_outcome,
        clean_outcome=phase_def.clean_outcome,
        issues_outcome=phase_def.issues_outcome,
        is_entry=(phase_name == pipeline.entry_phase),
        is_terminal=(phase_name == pipeline.terminal_phase),
        verification=verification_expl,
        has_parallelization=phase_def.parallelization is not None,
        post_commit_routes_info=[
            (route.when.budget_state, route.target)
            for route in pipeline.post_commit_routes
            if route.when.phase == phase_name
        ],
        workflow_fallback=workflow_fallback_info,
    )


def explain_policy(bundle: PolicyBundle) -> PolicyExplanation:
    """Convert a PolicyBundle into a structured human-readable explanation."""
    pipeline = bundle.pipeline
    agents = bundle.agents

    explanation = PolicyExplanation(
        entry_phase=pipeline.entry_phase,
        terminal_phase=pipeline.terminal_phase,
        entry_block=pipeline.entry_block,
        authored_blocks=sorted(pipeline.blocks.keys()),
    )

    for phase_name, phase_def in pipeline.phases.items():
        explanation.phases.append(_explain_phase(phase_name, phase_def, pipeline, agents))

    for completion_phase, lifecycle in pipeline.lifecycle_phases.items():
        explanation.lifecycle_explanations.append(
            LifecycleExplanation(
                lifecycle_name=lifecycle.lifecycle_name,
                completion_phase=completion_phase,
                completion_block=lifecycle.completion_block,
                increments_counter=lifecycle.increments_counter,
                before_complete=list(lifecycle.before_complete),
                after_complete=list(lifecycle.after_complete),
            )
        )

    for phase_name, phase_def in pipeline.phases.items():
        if phase_def.role == "terminal" and phase_def.terminal_outcome is not None:
            explanation.terminal_outcomes.append(
                TerminalOutcomeExplanation(
                    phase=phase_name,
                    outcome=phase_def.terminal_outcome,
                )
            )

    for lc_name, lc_cfg in pipeline.loop_counters.items():
        explanation.loop_counters.append(
            LoopCounterExplanation(
                name=lc_name,
                default_max=lc_cfg.default_max,
                description=lc_cfg.description,
            )
        )

    for bc_name, bc_cfg in pipeline.budget_counters.items():
        explanation.budget_counters.append(
            BudgetCounterExplanation(
                name=bc_name,
                description=bc_cfg.description,
                tracks_budget=bc_cfg.tracks_budget,
                default_max=bc_cfg.default_max,
            )
        )

    for route in pipeline.post_commit_routes:
        explanation.post_commit_routes.append(
            PostCommitRouteExplanation(
                phase=route.when.phase,
                budget_state=route.when.budget_state,
                target=route.target,
            )
        )

    for phase_name, phase_def in pipeline.phases.items():
        if phase_def.parallelization is None:
            continue
        pe = phase_def.parallelization
        pe_expl = ParallelExplanation(
            phase=phase_name,
            max_parallel_workers=pe.max_parallel_workers,
            max_work_units=pe.max_work_units,
            require_allowed_directories=pe.require_allowed_directories,
            post_fanout_verification=pe.post_fanout_verification,
        )
        explanation.parallel_executions.append(pe_expl)
        if explanation.parallel_execution is None:
            explanation.parallel_execution = pe_expl

    r = pipeline.recovery
    explanation.recovery = RecoveryExplanation(
        cycle_cap=r.cycle_cap,
        terminal_recovery_route=r.failed_route,
        preserve_session_on_categories=list(r.preserve_session_on_categories),
    )

    return explanation


__all__ = [
    "BudgetCounterExplanation",
    "CommitPolicyExplanation",
    "LifecycleExplanation",
    "LoopCounterExplanation",
    "LoopPolicyExplanation",
    "ParallelExplanation",
    "PhaseExplanation",
    "PolicyExplanation",
    "PostCommitRouteExplanation",
    "RecoveryExplanation",
    "TerminalOutcomeExplanation",
    "VerificationExplanation",
    "explain_policy",
    "explain_routing_decision",
]
