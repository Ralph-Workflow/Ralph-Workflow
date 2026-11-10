"""Public seam for the retained-integration diagnostic.

When a retained integration has no completion receipt, the diagnostic path
decides whether the retained evidence is still trustworthy. This module is
the public entry point for that path:

* :func:`diagnose_retained_integration` inspects the missing evidence under
  both worktree leases and never accepts an agent's success as proof;
* :func:`handoff_integration_diagnostic` offers inspection candidates by
  health without authorizing publication or cleanup;
* :func:`check_target_worktree_context` reports whether a target worktree
  loads its config, policy, and agent registry cleanly;
* :func:`next_resolver` picks the next resolver to call, or says why none
  is eligible.

The implementation lives in
:mod:`ralph.pipeline._integration_diagnostic` and
:mod:`ralph.pipeline._integration_diagnostic_handoff`; this module publishes
the entry points under a public module path so callers never have to reach
into a private one.
"""

from __future__ import annotations

from ralph.pipeline._integration_diagnostic import (
    diagnose_retained_integration,
    handoff_integration_diagnostic,
)
from ralph.pipeline._integration_diagnostic_handoff import (
    check_target_worktree_context,
    next_resolver,
)
from ralph.pipeline._pending_merge_repair import (
    handoff_pending_merge_repair,
    repair_pending_merge,
)

__all__ = [
    "check_target_worktree_context",
    "diagnose_retained_integration",
    "handoff_integration_diagnostic",
    "handoff_pending_merge_repair",
    "next_resolver",
    "repair_pending_merge",
]
