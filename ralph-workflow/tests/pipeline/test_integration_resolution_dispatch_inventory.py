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
from ralph.pipeline.plumbing import commit_plumbing, smoke_plumbing
from ralph.project_policy import cli_integration

ORDINARY_DISPATCH_ROUTES = (
    (run_loop._run_inner_loop, "_block_unresolved_integration"),
    (run_loop._continue_after_startup_integration, "_block_unresolved_integration"),
    (run_loop._run_inner_loop_after_startup, "_block_unresolved_integration"),
    (run_loop._resume_after_cooldown_wait, "inspect_integration_resolution"),
    (runner._run_pipeline_step, "determine_effect_from_policy"),
    (runner._integrate_inline_effect, "_integrate_on_phase_transition"),
    (effect_executor.execute_agent_effect, "assert_non_resolution_dispatch_allowed"),
    (worker_runtime.run_parallel_worker_from_manifest, "assert_non_resolution_dispatch_allowed"),
    (commit_plumbing._run_commit_agent_attempt_with_recovery, "execute_agent_effect"),
    (smoke_plumbing._execute_smoke_turns, "execute_agent_effect"),
    (cli_integration._make_production_invoke_agent, "execute_agent_effect"),
)

RESOLUTION_ROUTES = (
    session.invoke_resolution_agent,
    driver.run_rebase_conflict_resolution_pipeline,
)

EXPECTED_EFFECT_EXECUTOR_CALLER_MODULES = {
    "ralph.pipeline.runner",
    "ralph.pipeline.conflict_resolution.session",
    "ralph.pipeline.parallel.worker_runtime",
    "ralph.pipeline.plumbing.commit_plumbing",
    "ralph.pipeline.plumbing.smoke_plumbing",
    "ralph.project_policy.cli_integration",
}


def test_dispatch_inventory_reconciles_every_named_route() -> None:
    """Every ordinary route delegates to the shared verdict or final fence."""
    assert inspect_integration_resolution.__module__ == "ralph.pipeline.integration_resolution"
    for route, owner in ORDINARY_DISPATCH_ROUTES:
        assert owner in inspect.getsource(route), f"{route.__module__}.{route.__name__} lacks {owner}"


def test_every_live_effect_executor_caller_is_inventoried() -> None:
    """Adding an ordinary executor caller requires an explicit inventory decision."""
    callers = {
        module.__name__
        for module in (
            runner,
            session,
            worker_runtime,
            commit_plumbing,
            smoke_plumbing,
            cli_integration,
        )
        if "execute_agent_effect(" in inspect.getsource(module)
    }
    assert callers == EXPECTED_EFFECT_EXECUTOR_CALLER_MODULES


def test_resolution_routes_are_explicitly_out_of_graph_recovery() -> None:
    """Resolver invocations remain distinct from ordinary phase dispatch."""
    for route in RESOLUTION_ROUTES:
        source = inspect.getsource(route)
        assert "PHASE_RESOLUTION" in source or "_run_rounds" in source
