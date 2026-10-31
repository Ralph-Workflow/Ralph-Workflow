"""WU-3 / E6.4: arithmetic-dominance proof for the two restart bounds.

E6.4 requires BOTH bounds to be *proven*, not merely present:

(i)  worst-case time to terminal exhaustion with the OLD CHILD DEAD
     (the per-bridge "worst case terminal exhaustion dead child" bound), and
(ii) the delay a candidate can impose on the agent with the OLD CHILD ALIVE
     (the "candidate delay bound alive child" bound).

A bare ``value > 0`` check is vacuous for that purpose: every sum of
positive terms is positive, so positivity cannot distinguish "this is
an upper bound over its stages" from "someone dropped a term". This
module pins the DOMINANCE property instead — each bound must be at
least the sum of every worst-case duration it is composed of — and it
pins the computed values against numbers recomputed here by hand
rather than by re-evaluating the module's own expression.

Every value under test is read from the bridge's public ``diagnostics()``
snapshot, and the dominance rule itself is the single published
implementation in ``ralph.mcp.bridge_diagnostics.bound_dominates`` — the
exact function the lifecycle's import-time guard evaluates.

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

import pathlib

from ralph.mcp.bridge_diagnostics import bound_dominates
from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
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
#: ``candidate_deadline + ack + kill_wait + drain + teardown``, where the
#: candidate deadline is boot (15.0) plus readiness (5.0):
#: ``== 20.0 + 1.0 + 1.0 + 0.5 + 12.0 == 34.5``.
_E64_BOUND_B_PINNED_S = 34.5


class _LiveProcess:
    """Minimal process handle: alive, never polled for a real child."""

    def poll(self) -> int | None:
        return None

    @property
    def pid(self) -> int:
        return 4242

    def terminate(self, grace_period_s: float = 5.0) -> None:
        return None

    def kill(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0


def _bridge(tmp_path: pathlib.Path) -> lifecycle.RestartAwareMcpBridge:
    """Return a bridge whose public diagnostics snapshot carries the bounds.

    The session file lives inside pytest's ``tmp_path`` fixture, so the
    directory is torn down with the test session instead of leaking an
    unmanaged directory onto the host.
    """
    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    inner = lifecycle.StandaloneMcpProcess(
        endpoint="http://127.0.0.1:9100/mcp",
        process=_LiveProcess(),
        session_file=session_file,
    )
    return lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: inner,
        restart_policy=McpRestartPolicy(max_restarts=20, cooldown_seconds=2.0),
        run_id="test-e64",
    )


def test_e64_dominance_predicate_rejects_a_bound_that_drops_a_stage() -> None:
    """The predicate must reject an under-counted bound (the vacuity proof)."""
    # Under-counted: the drain term is omitted from the bound.
    assert bound_dominates(7.0, (5.0, 1.0, 1.0, 0.5)) is False
    # Under-counted: only the first stage is accounted for.
    assert bound_dominates(5.0, (5.0, 1.0)) is False
    # A bound that is not even positive.
    assert bound_dominates(0.0, (5.0, 1.0)) is False
    # A non-positive component can never be dominated meaningfully.
    assert bound_dominates(7.5, (5.0, -1.0)) is False
    # Sound: the exact sum of the components is a valid upper bound.
    assert bound_dominates(7.5, (5.0, 1.0, 1.0, 0.5)) is True
    # Sound: strictly more than the sum is also a valid upper bound.
    assert bound_dominates(9.0, (5.0, 1.0, 1.0, 0.5)) is True


def test_e64_bound_a_dominates_every_stage_it_is_composed_of(tmp_path: pathlib.Path) -> None:
    """Bound (i) must dominate the sum of its per-attempt stage durations."""
    diagnostics = _bridge(tmp_path).diagnostics()
    components = diagnostics.bound_a_components_s
    assert components == (2.0, 30.0, 12.0)
    assert bound_dominates(
        diagnostics.worst_case_terminal_exhaustion_dead_child_s / 20.0, components
    )


def test_e64_bound_b_dominates_every_stage_it_is_composed_of(tmp_path: pathlib.Path) -> None:
    """Bound (ii) must dominate the sum of its per-stage durations."""
    diagnostics = _bridge(tmp_path).diagnostics()
    components = diagnostics.bound_b_components_s
    assert components == (20.0, 1.0, 1.0, 0.5, 12.0)
    assert bound_dominates(diagnostics.candidate_delay_bound_alive_child_s, components)


def test_e64_cleanup_term_is_derived_from_the_real_shutdown_bounds(tmp_path: pathlib.Path) -> None:
    """The teardown term must track the constants the teardown path actually uses.

    This is the acceptance-review fix: the previous inventory declared only
    ``cooldown + preflight`` while the failed-startup path also pays
    ``StandaloneMcpProcess.shutdown``. Recomputing the term from the source
    constants means a future drift in either the process-manager policy or the
    standalone shutdown fails here instead of silently under-reporting.
    """
    diagnostics = _bridge(tmp_path).diagnostics()
    expected_cleanup = (
        SHUTDOWN_TERMINATE_GRACE_S
        + ProcessManagerPolicy.kill_followup_timeout_s
        + SHUTDOWN_REAP_WAIT_S
    )
    assert expected_cleanup == diagnostics.failed_attempt_cleanup_s
    assert expected_cleanup in diagnostics.bound_a_components_s
    assert expected_cleanup in diagnostics.bound_b_components_s


def test_e64_import_time_check_rejects_a_bound_that_drops_the_cleanup_term(
    tmp_path: pathlib.Path,
) -> None:
    """Dropping the teardown term must make the dominance predicate fail.

    Without this, a bound computed over the shortened component list would
    still be positive and would pass the import-time ``if``/``raise`` gate
    while under-reporting the real worst case.
    """
    diagnostics = _bridge(tmp_path).diagnostics()
    cleanup = diagnostics.failed_attempt_cleanup_s
    under_counted_a = diagnostics.worst_case_terminal_exhaustion_dead_child_s / 20.0 - cleanup
    assert bound_dominates(under_counted_a, diagnostics.bound_a_components_s) is False
    assert bound_dominates(
        diagnostics.worst_case_terminal_exhaustion_dead_child_s / 20.0,
        diagnostics.bound_a_components_s,
    )

    under_counted_b = (
        diagnostics.candidate_delay_bound_alive_child_s
        - cleanup
        - diagnostics.bound_b_components_s[-2]
    )
    assert bound_dominates(under_counted_b, diagnostics.bound_b_components_s) is False
    assert bound_dominates(
        diagnostics.candidate_delay_bound_alive_child_s,
        diagnostics.bound_b_components_s,
    )


def test_e64_bounds_match_independently_recomputed_numbers(tmp_path: pathlib.Path) -> None:
    """Pin both bounds against hand arithmetic, not a mirror of the module expression."""
    diagnostics = _bridge(tmp_path).diagnostics()
    assert McpRestartPolicy.max_restarts == 20
    assert McpRestartPolicy.cooldown_seconds == 2.0
    assert diagnostics.preflight_deadline_s == 30.0
    assert diagnostics.candidate_deadline_s == 20.0  # 15.0 boot allowance + 5.0 readiness
    assert diagnostics.handover_ack_timeout_s == 1.0
    assert diagnostics.handover_kill_wait_s == 1.0
    assert diagnostics.handover_drain_s == 0.5
    assert SHUTDOWN_TERMINATE_GRACE_S == 5.0
    assert ProcessManagerPolicy.kill_followup_timeout_s == 2.0
    assert SHUTDOWN_REAP_WAIT_S == 5.0

    assert diagnostics.failed_attempt_cleanup_s == 12.0

    assert diagnostics.worst_case_terminal_exhaustion_dead_child_s == _E64_BOUND_A_PINNED_S
    assert diagnostics.candidate_delay_bound_alive_child_s == _E64_BOUND_B_PINNED_S
