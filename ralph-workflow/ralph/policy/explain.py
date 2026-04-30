"""Policy explanation — converts a PolicyBundle into a structured human-readable description.

This module answers the key questions a user has when looking at an unfamiliar policy:
- What happens after this phase succeeds?
- What makes a phase terminal?
- When is a commit required?
- When does the system retry vs fall back vs fail?
- When is parallel execution allowed?
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle


@dataclass
class PhaseExplanation:
    """Explanation of a single phase."""

    name: str
    role: str | None
    drain: str
    chain: str | None
    agents: list[str]
    max_retries: int
    skip_invocation: bool
    on_success: str | None
    on_failure: str | None
    on_loopback: str | None
    bypass_routes: dict[str, str]
    decisions: dict[str, str]
    loop_policy: LoopPolicyExplanation | None
    commit_policy: CommitPolicyExplanation | None
    terminal_outcome: str | None
    clean_outcome: str | None = None
    issues_outcome: str | None = None
    is_entry: bool = False
    is_terminal: bool = False


@dataclass
class LoopPolicyExplanation:
    """Explanation of a phase's loop policy."""

    max_iterations: int
    iteration_state_field: str
    loopback_review_outcome: str | None


@dataclass
class CommitPolicyExplanation:
    """Explanation of a phase's commit policy."""

    increments_counter: str | None
    loop_resets: list[str]
    requires_artifact: bool


@dataclass
class LoopCounterExplanation:
    """Explanation of a loop counter."""

    name: str
    default_max: int
    description: str


@dataclass
class BudgetCounterExplanation:
    """Explanation of a budget counter."""

    name: str
    description: str
    tracks_budget: bool


@dataclass
class TerminalOutcomeExplanation:
    """Explanation of a terminal phase outcome."""

    phase: str
    outcome: str


@dataclass
class ParallelExplanation:
    """Explanation of the parallel execution policy."""

    phase: str
    max_parallel_workers: int
    max_work_units: int
    require_allowed_directories: bool


@dataclass
class RecoveryExplanation:
    """Explanation of the recovery policy."""

    cycle_cap: int
    terminal_recovery_route: str
    preserve_session_on_categories: list[str]


@dataclass
class PolicyExplanation:
    """Complete structured explanation of a PolicyBundle."""

    entry_phase: str
    terminal_phase: str
    phases: list[PhaseExplanation] = field(default_factory=list)
    loop_counters: list[LoopCounterExplanation] = field(default_factory=list)
    budget_counters: list[BudgetCounterExplanation] = field(default_factory=list)
    terminal_outcomes: list[TerminalOutcomeExplanation] = field(default_factory=list)
    parallel_execution: ParallelExplanation | None = None
    recovery: RecoveryExplanation | None = None


def explain_policy(bundle: PolicyBundle) -> PolicyExplanation:
    """Convert a PolicyBundle into a structured human-readable explanation.

    Args:
        bundle: The loaded policy bundle to explain.

    Returns:
        PolicyExplanation with all phases, counters, and routing rules described.
    """
    pipeline = bundle.pipeline
    agents = bundle.agents

    explanation = PolicyExplanation(
        entry_phase=pipeline.entry_phase,
        terminal_phase=pipeline.terminal_phase,
    )

    # Build phase explanations
    for phase_name, phase_def in pipeline.phases.items():
        drain_name = phase_def.drain
        drain_config = agents.agent_drains.get(drain_name)
        chain_name = drain_config.chain if drain_config else None
        chain_config = agents.agent_chains.get(chain_name) if chain_name else None

        effective_retry = pipeline.effective_retry_policy(phase_name)

        loop_expl: LoopPolicyExplanation | None = None
        if phase_def.loop_policy is not None:
            lp = phase_def.loop_policy
            loop_expl = LoopPolicyExplanation(
                max_iterations=lp.max_iterations,
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

        decision_targets = {
            dk: dr.target for dk, dr in phase_def.decisions.items()
        }

        phase_expl = PhaseExplanation(
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
            decisions=decision_targets,
            loop_policy=loop_expl,
            commit_policy=commit_expl,
            terminal_outcome=phase_def.terminal_outcome,
            clean_outcome=phase_def.clean_outcome,
            issues_outcome=phase_def.issues_outcome,
            is_entry=(phase_name == pipeline.entry_phase),
            is_terminal=(phase_name == pipeline.terminal_phase),
        )
        explanation.phases.append(phase_expl)

    # Terminal outcomes — all phases with role='terminal' and a declared outcome
    for phase_name, phase_def in pipeline.phases.items():
        if phase_def.role == "terminal" and phase_def.terminal_outcome is not None:
            explanation.terminal_outcomes.append(
                TerminalOutcomeExplanation(
                    phase=phase_name,
                    outcome=phase_def.terminal_outcome,
                )
            )

    # Loop counters — use lc_name/lc_cfg to avoid type narrowing conflict with budget loop
    for lc_name, lc_cfg in pipeline.loop_counters.items():
        explanation.loop_counters.append(
            LoopCounterExplanation(
                name=lc_name,
                default_max=lc_cfg.default_max,
                description=lc_cfg.description,
            )
        )

    # Budget counters — use bc_name/bc_cfg to avoid type narrowing conflict with loop counters
    for bc_name, bc_cfg in pipeline.budget_counters.items():
        explanation.budget_counters.append(
            BudgetCounterExplanation(
                name=bc_name,
                description=bc_cfg.description,
                tracks_budget=bc_cfg.tracks_budget,
            )
        )

    # Parallel execution
    for phase_name, phase_def in pipeline.phases.items():
        if phase_def.parallelization is None:
            continue
        pe = phase_def.parallelization
        explanation.parallel_execution = ParallelExplanation(
            phase=phase_name,
            max_parallel_workers=pe.max_parallel_workers,
            max_work_units=pe.max_work_units,
            require_allowed_directories=pe.require_allowed_directories,
        )
        break

    # Recovery
    r = pipeline.recovery
    explanation.recovery = RecoveryExplanation(
        cycle_cap=r.cycle_cap,
        terminal_recovery_route=r.failed_route,
        preserve_session_on_categories=list(r.preserve_session_on_categories),
    )

    return explanation
