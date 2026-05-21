"""Complete structured explanation of a PolicyBundle."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .budget_counter_explanation import BudgetCounterExplanation
    from .lifecycle_explanation import LifecycleExplanation
    from .loop_counter_explanation import LoopCounterExplanation
    from .parallel_explanation import ParallelExplanation
    from .phase_explanation import PhaseExplanation
    from .post_commit_route_explanation import PostCommitRouteExplanation
    from .recovery_explanation import RecoveryExplanation
    from .terminal_outcome_explanation import TerminalOutcomeExplanation


@dataclass
class PolicyExplanation:
    """Complete structured explanation of a PolicyBundle."""

    entry_phase: str
    terminal_phase: str
    entry_block: str | None = None
    authored_blocks: list[str] = field(default_factory=list)
    lifecycle_explanations: list[LifecycleExplanation] = field(default_factory=list)
    phases: list[PhaseExplanation] = field(default_factory=list)
    loop_counters: list[LoopCounterExplanation] = field(default_factory=list)
    budget_counters: list[BudgetCounterExplanation] = field(default_factory=list)
    terminal_outcomes: list[TerminalOutcomeExplanation] = field(default_factory=list)
    parallel_execution: ParallelExplanation | None = None
    parallel_executions: list[ParallelExplanation] = field(default_factory=list)
    post_commit_routes: list[PostCommitRouteExplanation] = field(default_factory=list)
    recovery: RecoveryExplanation | None = None
