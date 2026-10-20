"""WU-3 / E6.4: arithmetic-dominance proof for the two restart bounds.

E6.4 requires BOTH bounds to be *proven*, not merely present:

(i)  worst-case time to terminal exhaustion with the OLD CHILD DEAD
     (``_WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S``), and
(ii) the delay a candidate can impose on the agent with the OLD CHILD ALIVE
     (``_CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S``).

A bare ``value > 0`` check is vacuous for that purpose: every sum of
positive terms is positive, so positivity cannot distinguish "this is
an upper bound over its stages" from "someone dropped a term". This
module pins the DOMINANCE property instead — each bound must be at
least the sum of every worst-case duration it is composed of — and it
pins the computed values against numbers recomputed here by hand
rather than by re-evaluating the module's own expression.

The import-time invariant is enforced by ``if/raise RuntimeError`` (NOT
``assert``) so the guard survives ``python -O``. WU-3 added a separate
behavioural proof of that property in
``tests/mcp/test_e64_import_guard_execution.py`` (a real subprocess
that imports the lifecycle module, computes an invalid bound, and
exercises the ``raise RuntimeError`` path under both normal Python and
``python -O``); the older source-substring / AST-only tests that
predated WU-3 are removed from this file because they cannot fail on
a behavioural regression.
"""

from __future__ import annotations

from ralph.mcp.server import lifecycle
from ralph.mcp.server._handover_protocol import (
    _HANDOVER_ACK_TIMEOUT_S,
    _HANDOVER_DRAIN_S,
)
from ralph.mcp.server._standalone_mcp_process import (
    SHUTDOWN_REAP_WAIT_S,
    SHUTDOWN_TERMINATE_GRACE_S,
)
from ralph.process.manager import ProcessManagerPolicy

#: Hand-computed worst case with the old child dead:
#: ``max_restarts * (cooldown + preflight + teardown)``
#: ``== 20 * (2.0 + 30.0 + 12.0) == 880.0`` where the teardown term is
#: ``5.0`` (graceful SIGTERM window) + ``2.0`` (post-SIGKILL follow-up)
#: + ``5.0`` (terminal reap wait).
_E64_BOUND_A_PINNED_S = 880.0
#: Hand-computed candidate delay with the old child alive:
#: ``candidate_readiness + ack + kill_wait + drain + teardown``
#: ``== 5.0 + 1.0 + 1.0 + 0.5 + 12.0 == 19.5``.
_E64_BOUND_B_PINNED_S = 19.5


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
    assert components == (2.0, 30.0, 12.0)
    assert lifecycle._e64_bound_dominates(
        lifecycle._WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S / 20.0, components
    )


def test_e64_bound_b_dominates_every_stage_it_is_composed_of() -> None:
    """Bound (ii) must dominate the sum of its per-stage durations."""
    components = lifecycle._E64_BOUND_B_COMPONENTS_S
    assert components == (5.0, 1.0, 1.0, 0.5, 12.0)
    assert lifecycle._e64_bound_dominates(
        lifecycle._CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S, components
    )


def test_e64_cleanup_term_is_derived_from_the_real_shutdown_bounds() -> None:
    """The teardown term must track the constants the teardown path actually uses.

    This is the acceptance-review fix: the previous inventory declared only
    ``cooldown + preflight`` while the failed-startup path also pays
    ``StandaloneMcpProcess.shutdown``. Recomputing the term from the source
    constants means a future drift in either the process-manager policy or the
    standalone shutdown fails here instead of silently under-reporting.
    """
    expected_cleanup = (
        SHUTDOWN_TERMINATE_GRACE_S
        + ProcessManagerPolicy.kill_followup_timeout_s
        + SHUTDOWN_REAP_WAIT_S
    )
    assert expected_cleanup == lifecycle._FAILED_ATTEMPT_CLEANUP_S
    assert expected_cleanup in lifecycle._E64_BOUND_A_COMPONENTS_S
    assert expected_cleanup in lifecycle._E64_BOUND_B_COMPONENTS_S


def test_e64_import_time_check_rejects_a_bound_that_drops_the_cleanup_term() -> None:
    """Dropping the teardown term must make the dominance predicate fail.

    Without this, a bound computed over the shortened component list would
    still be positive and would pass the import-time ``if``/``raise`` gate
    while under-reporting the real worst case.
    """
    cleanup = lifecycle._FAILED_ATTEMPT_CLEANUP_S
    under_counted_a = lifecycle._WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S / 20.0 - cleanup
    assert (
        lifecycle._e64_bound_dominates(under_counted_a, lifecycle._E64_BOUND_A_COMPONENTS_S)
        is False
    )
    assert lifecycle._e64_bound_dominates(
        lifecycle._WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S / 20.0,
        lifecycle._E64_BOUND_A_COMPONENTS_S,
    )

    under_counted_b = (
        lifecycle._CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S
        - cleanup
        - lifecycle._E64_BOUND_B_COMPONENTS_S[-2]
    )
    assert (
        lifecycle._e64_bound_dominates(under_counted_b, lifecycle._E64_BOUND_B_COMPONENTS_S)
        is False
    )
    assert lifecycle._e64_bound_dominates(
        lifecycle._CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S,
        lifecycle._E64_BOUND_B_COMPONENTS_S,
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
    assert SHUTDOWN_TERMINATE_GRACE_S == 5.0
    assert ProcessManagerPolicy.kill_followup_timeout_s == 2.0
    assert SHUTDOWN_REAP_WAIT_S == 5.0

    assert lifecycle._FAILED_ATTEMPT_CLEANUP_S == 12.0

    assert lifecycle._WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S == _E64_BOUND_A_PINNED_S
    assert lifecycle._CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S == _E64_BOUND_B_PINNED_S
    # The aliases are the same numbers, not two drifting copies.
    assert lifecycle._WORST_CASE_TIME_TO_TERMINAL_EXHAUSTION_S == _E64_BOUND_A_PINNED_S
    assert lifecycle._CANDIDATE_DELAY_BOUND_S == _E64_BOUND_B_PINNED_S
