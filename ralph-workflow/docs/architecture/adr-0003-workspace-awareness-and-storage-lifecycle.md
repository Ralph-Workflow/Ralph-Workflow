# ADR-0003: Workspace awareness and storage lifecycle

## Architecture

`WorkspaceMonitor` owns the single recursive watch per process-local workspace and shares it across monitor leases. `WorkspaceAwareness` coalesces the bounded dirty set, records explicit watch or live-fallback freshness, and hands changes to the persisted Explore dirty queue at lifecycle boundaries. `ReindexWriter` remains the single-writer coalescing boundary and only publishes completed generations.

Storage remains categorised as project content, workflow records, workspace intelligence, operational records, and temporary data. Project content and required records are protected; derived intelligence and inactive temporary data are disposable through the side-effect-free `inventory_storage` inventory and `plan_cleanup` cleanup planner. `inventory_storage` covers every accumulating writer path discovered by an AST walk of `ralph/workspace/`, `ralph/mcp/artifacts/`, and `ralph/mcp/explore/`, each row carrying `growth_trigger`, `retention_basis`, `eligibility_reason`, `recreatable`, `recovery_impact`, and `active_owner`.

Retention sweeps coalesce through `RetentionPassCoordinator` — one inner pass per in-process wave. The process-local active-run registry (`register_active_run` / `unregister_active_run`, driven from the pipeline run command) guarantees that a sweep never removes an active workflow's receipts, sentinels, or DB rows. `ralph workspace-health` exposes the composed read-only operator view (storage, freshness, readiness, watch capacity, cleanup eligibility) without mutating the workspace.

## Boundaries

Unchanged warm refreshes retain zero-parse behavior. Watch-capacity failure is visible as `live_fallback`, never `current`; a later workspace lease can recover observation. Process-local sharing is the safe coordination boundary: cross-process watch leasing and independent-process retention coordination are not supported.

## Verification

`tests/agents/test_workspace_watch_scoping.py`, `tests/test_filesystem_activity_baseline.py`, `tests/test_explore_lifecycle.py`, `tests/test_explore_pipeline.py`, `tests/test_explore_bench_gates.py`, `tests/unit/test_agent_dir_retention.py`, `tests/unit/test_storage_lifecycle.py`, and `tests/test_cli_workspace_health.py` cover the behavior. `make -C ralph-workflow verify` is the authoritative gate.
