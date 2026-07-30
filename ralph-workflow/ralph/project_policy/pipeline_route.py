"""One routing edge in the hardcoded policy pipeline.

Mirrors :class:`ralph.policy.models.PhaseDecisionRoute` — the in-graph pipeline's
decision route — so the out-of-graph policy pipeline expresses routing with the
same vocabulary the real pipeline uses. It is deliberately a plain frozen
dataclass rather than the Pydantic model: the policy pipeline is hardcoded, so
there is no TOML to validate and no need to pay for validation of a table that
cannot change at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PolicyRoute:
    """Where a decision sends the policy pipeline, and what it does to the loop.

    Attributes:
        target: The phase this decision routes to. May be the terminal phase.
        reset_loop: True when this route is a FORWARD exit from the analysis
            loop, which zeroes the iteration counter (matching
            ``reset_loop = true`` in the in-graph pipeline's decision tables).
            False when the route is a loopback, which consumes one iteration of
            the analysis budget.
    """

    target: str
    reset_loop: bool = False


__all__ = ["PolicyRoute"]
