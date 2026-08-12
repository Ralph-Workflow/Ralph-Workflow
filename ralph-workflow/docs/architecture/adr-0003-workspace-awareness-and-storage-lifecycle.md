# ADR-0003: Workspace awareness and storage lifecycle

- Status: accepted
- Date: 2026-08-12

## Context

Workspace observation, indexed search, and Ralph-managed storage must remain useful without scaling host watch registrations, repeated scans, or retained data with workflow count. A failed observer must not cause search to claim current knowledge.

## Decision

`WorkspaceMonitor` owns the single recursive watch per process-local workspace and shares it across monitor leases. `WorkspaceAwareness` coalesces the bounded dirty set, records explicit watch or live-fallback freshness, and hands changes to the persisted Explore dirty queue at lifecycle boundaries. `ReindexWriter` remains the single-writer coalescing boundary and only publishes completed generations.

Storage remains categorised as project content, workflow records, workspace intelligence, operational records, and temporary data. Project content and required records are protected; derived intelligence and inactive temporary data are disposable through the existing side-effect-free inventory and cleanup planner.

## Consequences

Unchanged warm refreshes retain zero-parse behavior. Watch-capacity failure is visible as `live_fallback`, never `current`; a later workspace lease can recover observation. Process-local sharing is the current safe coordination boundary; cross-process watch leasing remains explicitly outside this decision until a durable owner protocol has deterministic proof.

## Verification

`tests/agents/test_workspace_watch_scoping.py`, `tests/test_filesystem_activity_baseline.py`, `tests/test_explore_lifecycle.py`, `tests/test_explore_pipeline.py`, `tests/test_explore_bench_gates.py`, and `tests/unit/test_agent_dir_retention.py` cover the behavior. `make -C ralph-workflow verify` is the authoritative gate.
