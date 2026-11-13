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

_E64_BOUND_A_PINNED_S = 880.0
_E64_BOUND_B_PINNED_S = 34.5


class _LiveProcess:
    def poll(self) -> int | None:
        return None

    @property
    def pid(self) -> int:
        return 4242

    def terminate(self, grace_period_s: float = 5.0, **_kwargs: object) -> None:
        return None

    def kill(self, **_kwargs: object) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0


def _bridge(tmp_path: pathlib.Path) -> lifecycle.RestartAwareMcpBridge:
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
    assert bound_dominates(7.0, (5.0, 1.0, 1.0, 0.5)) is False
    assert bound_dominates(5.0, (5.0, 1.0)) is False
    assert bound_dominates(0.0, (5.0, 1.0)) is False
    assert bound_dominates(7.5, (5.0, -1.0)) is False
    assert bound_dominates(7.5, (5.0, 1.0, 1.0, 0.5)) is True
    assert bound_dominates(9.0, (5.0, 1.0, 1.0, 0.5)) is True


def test_e64_bound_a_dominates_every_stage_it_is_composed_of(tmp_path: pathlib.Path) -> None:
    diagnostics = _bridge(tmp_path).diagnostics()
    components = diagnostics.bound_a_components_s
    assert components == (2.0, 30.0, 12.0)
    assert bound_dominates(
        diagnostics.worst_case_terminal_exhaustion_dead_child_s / 20.0, components
    )


def test_e64_bound_b_dominates_every_stage_it_is_composed_of(tmp_path: pathlib.Path) -> None:
    diagnostics = _bridge(tmp_path).diagnostics()
    components = diagnostics.bound_b_components_s
    assert components == (20.0, 1.0, 1.0, 0.5, 12.0)
    assert bound_dominates(diagnostics.candidate_delay_bound_alive_child_s, components)


def test_e64_cleanup_term_is_derived_from_the_real_shutdown_bounds(tmp_path: pathlib.Path) -> None:
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
    diagnostics = _bridge(tmp_path).diagnostics()
    assert McpRestartPolicy.max_restarts == 20
    assert McpRestartPolicy.cooldown_seconds == 2.0
    assert diagnostics.preflight_deadline_s == 30.0
    assert diagnostics.candidate_deadline_s == 20.0
    assert diagnostics.handover_ack_timeout_s == 1.0
    assert diagnostics.handover_kill_wait_s == 1.0
    assert diagnostics.handover_drain_s == 0.5
    assert SHUTDOWN_TERMINATE_GRACE_S == 5.0
    assert ProcessManagerPolicy.kill_followup_timeout_s == 2.0
    assert SHUTDOWN_REAP_WAIT_S == 5.0

    assert diagnostics.failed_attempt_cleanup_s == 12.0

    assert diagnostics.worst_case_terminal_exhaustion_dead_child_s == _E64_BOUND_A_PINNED_S
    assert diagnostics.candidate_delay_bound_alive_child_s == _E64_BOUND_B_PINNED_S
