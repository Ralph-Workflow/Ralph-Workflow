"""WU-2 / E6.4: arithmetic-dominance proof for the two required restart bounds.

E6.4 requires BOTH bounds to be *proven*, not merely present:

(i) worst-case time to terminal exhaustion with the OLD CHILD DEAD
    (``_WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S``), and
(ii) the delay a candidate can impose on the agent with the OLD CHILD ALIVE
    (``_CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S``).

A bare ``value > 0`` check is vacuous for that purpose: every sum of positive
terms is positive, so positivity can never distinguish "this is an upper bound
over its stages" from "someone dropped a term". This module pins the
DOMINANCE property instead — each bound must be at least the sum of every
worst-case duration it is composed of — and it pins the computed values
against numbers recomputed here by hand rather than by re-evaluating the
module's own expression.
"""

from __future__ import annotations

import ast
import inspect

from ralph.mcp.server import lifecycle
from ralph.mcp.server._handover_protocol import (
    _HANDOVER_ACK_TIMEOUT_S,
    _HANDOVER_DRAIN_S,
)

#: Hand-computed worst case with the old child dead:
#: ``max_restarts * (cooldown + preflight) == 20 * (2.0 + 30.0) == 640.0``.
_E64_BOUND_A_PINNED_S = 640.0
#: Hand-computed candidate delay with the old child alive:
#: ``candidate_readiness + ack + kill_wait + drain == 5.0 + 1.0 + 1.0 + 0.5``.
_E64_BOUND_B_PINNED_S = 7.5


def test_e64_dominance_predicate_rejects_a_bound_that_drops_a_stage() -> None:
    """The predicate must reject an under-counted bound (the vacuity proof)."""
    dominates = lifecycle._e64_bound_dominates

    # Under-counted: the drain term is omitted from the bound.
    assert dominates(7.0, (5.0, 1.0, 1.0, 0.5)) is False
    # Under-counted: only the first stage is accounted for.
    assert dominates(5.0, (5.0, 1.0)) is False
    # A bound that is not even positive.
    assert dominates(0.0, (5.0, 1.0)) is False
    # A non-positive component can never be dominated meaningfully.
    assert dominates(7.5, (5.0, -1.0)) is False
    # Sound: the exact sum of the components is a valid upper bound.
    assert dominates(7.5, (5.0, 1.0, 1.0, 0.5)) is True
    # Sound: strictly more than the sum is also a valid upper bound.
    assert dominates(9.0, (5.0, 1.0, 1.0, 0.5)) is True


def test_e64_bound_a_dominates_every_stage_it_is_composed_of() -> None:
    """Bound (i) must dominate the sum of its per-attempt stage durations."""
    components = lifecycle._E64_BOUND_A_COMPONENTS_S
    assert components == (2.0, 30.0)
    assert lifecycle._e64_bound_dominates(
        lifecycle._WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S / 20.0, components
    )


def test_e64_bound_b_dominates_every_stage_it_is_composed_of() -> None:
    """Bound (ii) must dominate the sum of its four handover stage durations."""
    components = lifecycle._E64_BOUND_B_COMPONENTS_S
    assert components == (5.0, 1.0, 1.0, 0.5)
    assert lifecycle._e64_bound_dominates(
        lifecycle._CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S, components
    )


def test_e64_bounds_match_independently_recomputed_numbers() -> None:
    """Pin both bounds against hand arithmetic, not a mirror of the module expression."""
    assert lifecycle.McpRestartPolicy.max_restarts == 20
    assert lifecycle.McpRestartPolicy.cooldown_seconds == 2.0
    assert lifecycle._PREFLIGHT_DEADLINE_S == 30.0
    assert lifecycle._CANDIDATE_DEADLINE_S == 5.0
    assert _HANDOVER_ACK_TIMEOUT_S == 1.0
    assert lifecycle._HANDOVER_KILL_WAIT_S == 1.0
    assert _HANDOVER_DRAIN_S == 0.5

    assert lifecycle._WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S == _E64_BOUND_A_PINNED_S
    assert lifecycle._CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S == _E64_BOUND_B_PINNED_S
    # The aliases are the same numbers, not two drifting copies.
    assert lifecycle._WORST_CASE_TIME_TO_TERMINAL_EXHAUSTION_S == _E64_BOUND_A_PINNED_S
    assert lifecycle._CANDIDATE_DELAY_BOUND_S == _E64_BOUND_B_PINNED_S


def test_e64_dominance_is_enforced_by_if_raise_not_assert() -> None:
    """A bare ``assert`` would be stripped by ``python -O``; require ``raise RuntimeError``."""
    source = inspect.getsource(lifecycle)
    assert "def _e64_bound_dominates(" in source
    assert "raise RuntimeError" in source
    # The dominance predicate itself must not be implemented with ``assert``.
    # Parsed as an AST so a docstring mentioning the word cannot mask a
    # real assert statement (and vice versa).
    predicate_tree = ast.parse(inspect.getsource(lifecycle._e64_bound_dominates))
    assert not [node for node in ast.walk(predicate_tree) if isinstance(node, ast.Assert)], (
        "the import-time dominance proof must not use assert (stripped by python -O)"
    )
    assert any(isinstance(node, ast.Return) for node in ast.walk(predicate_tree)), (
        "the predicate must return explicit booleans"
    )
