"""Same-workspace parallel pipeline coordination.

This package provides the core components for running Ralph Workflow development
phases in parallel across multiple worker processes within the same repository
checkout (same-workspace fan-out, v1).

Supported public surface:

- **ParallelExecutionMode**: Enumeration of supported parallel execution modes.
  Only ``SAME_WORKSPACE`` is supported in v1.
- **SameWorkspaceContext**: Configuration for a same-workspace fan-out run,
  including repo root, per-worker namespace root, MCP factory, and optional
  executor command.

These are the only supported parallel primitives for v1.
Per-worker branches and post-development branch reconciliation are explicitly
out of scope for this iteration.
"""

from ralph.pipeline.parallel.mode import ParallelExecutionMode, SameWorkspaceContext
from ralph.pipeline.parallel.worker_runtime import (
    WorkerRuntimePaths as WorkerRuntimePaths,
)
from ralph.pipeline.parallel.worker_runtime import (
    build_worker_runtime_paths as build_worker_runtime_paths,
)

__all__ = [
    "ParallelExecutionMode",
    "SameWorkspaceContext",
]
