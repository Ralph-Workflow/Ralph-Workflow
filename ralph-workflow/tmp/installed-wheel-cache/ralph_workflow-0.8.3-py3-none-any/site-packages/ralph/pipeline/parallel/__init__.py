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
- **validate_for_same_workspace**: Pre-flight validator that rejects overlapping,
  missing, or reserved edit areas before any worker is launched.

These are the only supported parallel primitives for v1.
Per-worker branches and post-development branch reconciliation are explicitly
out of scope for this iteration.
"""

from ralph.pipeline.parallel.mode import ParallelExecutionMode, SameWorkspaceContext
from ralph.pipeline.work_units import validate_for_same_workspace

__all__ = [
    "ParallelExecutionMode",
    "SameWorkspaceContext",
    "validate_for_same_workspace",
]
