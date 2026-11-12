from __future__ import annotations

import inspect

from ralph.pipeline import effect_executor, run_loop, runner
from ralph.pipeline.conflict_resolution import driver, session
from ralph.pipeline.integration_resolution import inspect_integration_resolution
from ralph.pipeline.parallel import worker_runtime
from ralph.pipeline.plumbing import commit_plumbing, smoke_plumbing
from ralph.project_policy import cli_integration

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_dispatch_inventory_reconciles_every_named_route": (
        "_run_inner_loop no longer calls _block_unresolved_integration directly."
        " The fail-closed dispatch fence moved into _continue_after_startup_integration."
        " _run_pipeline_step now reaches determine_effect_from_policy through"
        " the _prepare_pipeline_step_dispatch funnel."
    ),
    "test_resolution_routes_are_explicitly_out_of_graph_recovery": (
        "run_rebase_conflict_resolution_pipeline moved to pipeline_entrypoints.py."
        " The _run_rounds runner it delegates to now lives in the driver."
    ),
}

ORDINARY_DISPATCH_ROUTES = (
    (run_loop._run_inner_loop, "_continue_after_startup_integration"),
    (run_loop._continue_after_startup_integration, "_block_unresolved_integration"),
    (run_loop._run_inner_loop_after_startup, "_block_unresolved_integration"),
    (run_loop._resume_after_cooldown_wait, "inspect_integration_resolution"),
    (runner._run_pipeline_step, "_prepare_pipeline_step_dispatch"),
    (runner._prepare_pipeline_step_dispatch, "call_determine_effect_from_policy"),
    (runner._integrate_inline_effect, "_integrate_on_phase_transition"),
    (effect_executor.execute_agent_effect, "assert_non_resolution_dispatch_allowed"),
    (worker_runtime.run_parallel_worker_from_manifest, "assert_non_resolution_dispatch_allowed"),
    (commit_plumbing._run_commit_agent_attempt_with_recovery, "execute_agent_effect"),
    (smoke_plumbing._execute_smoke_turns, "execute_agent_effect"),
    (cli_integration._make_production_invoke_agent, "execute_agent_effect"),
)

RESOLUTION_ROUTES = (
    (session.invoke_resolution_agent, "PHASE_RESOLUTION"),
    (driver.run_rebase_conflict_resolution_pipeline, "run_rebase_conflict_resolution_outcome"),
    (driver.run_rebase_conflict_resolution_outcome, "_run_rounds"),
)

EXPECTED_EFFECT_EXECUTOR_CALLER_MODULES = {
    "ralph.pipeline.runner",
    "ralph.pipeline.conflict_resolution.session",
    "ralph.pipeline.parallel.worker_runtime",
    "ralph.pipeline.plumbing.commit_plumbing",
    "ralph.pipeline.plumbing.smoke_plumbing",
    "ralph.project_policy.cli_integration",
}


def _names_owner(route: object, owner: str) -> bool:
    source = inspect.getsource(route)
    if owner in source:
        return True
    module = inspect.getmodule(route)
    for name, member in inspect.getmembers(module, inspect.isfunction):
        if member.__module__ != route.__module__:
            continue
        if f"{name}(" not in source:
            continue
        if owner in inspect.getsource(member):
            return True
    return False


def test_dispatch_inventory_reconciles_every_named_route() -> None:
    assert inspect_integration_resolution.__module__ == "ralph.pipeline.integration_resolution"
    for route, owner in ORDINARY_DISPATCH_ROUTES:
        assert _names_owner(route, owner), f"{route.__module__}.{route.__name__} lacks {owner}"


def test_every_live_effect_executor_caller_is_inventoried() -> None:
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
    for route, mechanism in RESOLUTION_ROUTES:
        source = inspect.getsource(route)
        assert mechanism in source, f"{route.__module__}.{route.__name__} lacks {mechanism}"
