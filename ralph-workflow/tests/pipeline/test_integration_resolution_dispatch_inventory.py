"""Executable inventory of every integration-resolution dispatch route.

This test is deliberately structural: it keeps the plan's route inventory
co-located with executable proof that the only ordinary-phase verdict owner is
``integration_resolution.inspect_integration_resolution``.  Resolver routes
are listed separately because they are the sole legal recovery executor.
"""

from __future__ import annotations

import inspect

from ralph.pipeline import effect_executor, run_loop, runner
from ralph.pipeline.conflict_resolution import driver, session
from ralph.pipeline.integration_resolution import inspect_integration_resolution
from ralph.pipeline.parallel import worker_runtime


ORDINARY_DISPATCH_ROUTES = (
    (run_loop._run_inner_loop, "_block_unresolved_integration"),
    (run_loop._continue_after_startup_integration, "_block_unresolved_integration"),
    (run_loop._run_inner_loop_after_startup, "_block_unresolved_integration"),
    (run_loop._resume_after_cooldown_wait, "_reselect_preferred_agent"),
    (runner._run_pipeline_step, "determine_effect_from_policy"),
    (runner._integrate_inline_effect, "_integrate_on_phase_transition"),
    (effect_executor.execute_agent_effect, "assert_non_resolution_dispatch_allowed"),
    (worker_runtime.run_parallel_worker_from_manifest, "assert_non_resolution_dispatch_allowed"),
)

RESOLUTION_ROUTES = (
    session.invoke_resolution_agent,
    driver.run_rebase_conflict_resolution_pipeline,
)


def test_dispatch_inventory_reconciles_every_named_route() -> None:
    """Every ordinary route delegates to the shared verdict or final fence."""
    assert inspect_integration_resolution.__module__ == "ralph.pipeline.integration_resolution"
    for route, owner in ORDINARY_DISPATCH_ROUTES:
        assert owner in inspect.getsource(route), f"{route.__module__}.{route.__name__} lacks {owner}"


def test_resolution_routes_are_explicitly_out_of_graph_recovery() -> None:
    """Resolver invocations remain distinct from ordinary phase dispatch."""
    for route in RESOLUTION_ROUTES:
        source = inspect.getsource(route)
        assert "PHASE_RESOLUTION" in source or "_run_rounds" in source
