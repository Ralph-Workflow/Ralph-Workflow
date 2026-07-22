"""Out-of-graph pipeline that resolves auto-integration merge conflicts.

Auto-integration can stop on conflicts at four different seams (after a
commit, at any of eleven phase boundaries, at the fan-out join and at run
startup). None of those is a node in the main ``PipelinePolicy`` graph, so
conflict resolution cannot be a graph phase: it must be enterable from the
middle of a seam. This package is therefore a HARDCODED, out-of-graph
pipeline modelled on :mod:`ralph.project_policy` -- a pure phase graph
(:mod:`~ralph.pipeline.conflict_resolution.graph`), a prompt renderer, its
own status-bar surface, an MCP-backed agent session and a driver that loops
within a budget and never aborts the surrounding run.

The MCP-backed session is the load-bearing part. The resolver used to call
``invoke_agent`` directly with no ``RALPH_MCP_ENDPOINT``, which left the
agent with no Ralph tool surface, no ``declare_complete`` contract and no
exec-policy git denial. Running the invocation through
``effect_executor.execute_agent_effect`` (as
:mod:`ralph.project_policy.cli_integration` does) builds the session bridge,
so the completion contract and the git denial are both real.
"""

from __future__ import annotations

from ralph.pipeline.conflict_resolution.driver import (
    resolution_deadline,
    run_conflict_resolution_pipeline,
    run_rebase_conflict_resolution_pipeline,
)
from ralph.pipeline.conflict_resolution.rebase_loop import (
    RebaseStop,
    RebaseStopResolver,
    resolve_rebase_in_progress,
)

__all__ = [
    "RebaseStop",
    "RebaseStopResolver",
    "resolution_deadline",
    "resolve_rebase_in_progress",
    "run_conflict_resolution_pipeline",
    "run_rebase_conflict_resolution_pipeline",
]
