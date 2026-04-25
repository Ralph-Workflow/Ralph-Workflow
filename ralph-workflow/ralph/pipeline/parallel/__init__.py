"""Parallel pipeline coordination primitives.

This package provides the core components for running Ralph Workflow phases
in parallel across multiple worker processes:

- **Coordinator**: Manages the overall parallel execution lifecycle
- **Scheduler**: Distributes work units across available workers
- **WorkerSession**: Maintains state for an individual worker process
- **MergeIntegrator**: Combines results from parallel workers

These components are used internally by the pipeline orchestrator when
parallel execution is enabled via policy configuration.
"""
