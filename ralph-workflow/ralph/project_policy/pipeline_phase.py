"""One phase definition in the hardcoded policy pipeline.

Mirrors :class:`ralph.policy.models.PhaseDefinition` — drain, role, transitions,
decision table — restricted to the fields the two-phase policy pipeline actually
needs. See :mod:`ralph.project_policy.pipeline_graph` for the graph itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ralph.project_policy.pipeline_route import PolicyRoute


@dataclass(frozen=True, slots=True)
class PolicyPhaseDef:
    """A phase of the policy pipeline.

    Attributes:
        drain: The agent drain this phase invokes. Bound to a chain in
            ``ralph/policy/defaults/agents.toml``; both policy drains are
            out-of-graph (bound to no pipeline phase).
        role: ``"execution"`` or ``"analysis"``, matching the in-graph
            ``PhaseRole`` vocabulary. Only an analysis phase carries a decision
            table and consumes the loop budget.
        on_success: The phase reached when this phase completes. ``None`` for a
            phase that routes solely through its decision table.
        decisions: Decision string -> route. Populated only for an analysis
            phase; the keys are exactly the ``decision_vocabulary`` of the
            phase's artifact contract in ``artifacts.toml``.
    """

    drain: str
    role: str
    on_success: str | None = None
    decisions: dict[str, PolicyRoute] = field(default_factory=dict[str, PolicyRoute])


__all__ = ["PolicyPhaseDef"]
