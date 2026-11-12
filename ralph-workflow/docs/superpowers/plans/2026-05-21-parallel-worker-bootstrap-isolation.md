# Parallel worker bootstrap isolation

Ralph Workflow runs a single developer agent. Worker orchestration — splitting a
plan into work units and dispatching parallel "worker" agents — is deprecated;
parallelism is owned by the developer agent's own sub-agents, and the bundled
`dispatch_mode` is `agent_subagents` (`ralph/policy/defaults/pipeline.toml`).

The legacy `ralph_fan_out` dispatch mode remains parseable and routes work units
through the same-workspace fan-out machinery into `FanOutEffect`
(`ralph/pipeline/effect_router.py`). Fan-out writes one `ParallelWorkerManifest`
per work unit under `.agent/workers/<unit_id>/worker-manifest.json` and launches
each worker with the hidden `--parallel-worker-manifest` option
(`ralph/pipeline/fan_out.py`). The worker runtime loads that manifest and
materializes the worker prompt with the work unit and worker namespace
(`ralph/pipeline/parallel/worker_runtime.py`); materialization fails closed with
`ValueError` whenever a work unit or worker namespace is supplied
(`ralph/prompts/materialize.py`). Legacy workers therefore cannot execute end to
end, and the developer execution path is the only live end-to-end execution
route.

The current worker-orchestration contract is documented in
[concepts.md §'Deprecated: Ralph Workflow-orchestrated
workers'](../../sphinx/concepts.md#deprecated-ralph-orchestrated-workers).
