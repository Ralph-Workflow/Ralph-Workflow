"""Builds the conflict resolver handed to the auto-integrate step.

This module is now a thin adapter. It builds the production
:data:`~ralph.pipeline.auto_integrate_resolve.ConflictResolver` -- the
``(repo_root, target) -> bool`` callable
:func:`~ralph.pipeline.auto_integrate_resolve.endpoint_merge_with_resolution`
consumes -- and delegates the actual work to
:func:`ralph.pipeline.conflict_resolution.run_conflict_resolution_pipeline`,
the out-of-graph pipeline that owns prompting, status, session and the
bounded retry loop.

The delegation exists because the previous implementation invoked the
agent OUTSIDE Ralph's MCP session: it called ``invoke_agent`` directly
with no ``RALPH_MCP_ENDPOINT``, so there was no Ralph tool surface, no
``declare_complete`` contract and -- critically -- no exec policy. The
claim that "an agent running under Ralph's own MCP exec policy is denied
every git invocation" was therefore aspirational rather than true. It is
true now: the pipeline runs every round through
``effect_executor.execute_agent_effect``, which builds the session
bridge, so ``ralph.mcp.tools.exec.apply_exec_policy`` actually binds and
the agent cannot commit, abort or move a ref. Ralph alone stages the
previously-conflicted paths and creates the merge commit.

Fault-tolerance contract, unchanged: every failure mode returns ``False``
so the integration step aborts the merge and records a conflict instead
of crashing the run. A MISSING dependency (no ``pipeline_deps``, no
``workspace_scope``) is one of those failures -- it declines rather than
falling back to an MCP-less invocation, because that fallback is the
defect this module exists to remove.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.pipeline.conflict_resolution import run_conflict_resolution_pipeline
from ralph.pipeline.conflict_resolution.session import resolution_chain_agents

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Protocol

    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.factory import PipelineDeps
    from ralph.policy.models import PolicyBundle
    from ralph.workspace.scope import WorkspaceScope

    class _SupportsAgentLookup(Protocol):
        """Structural registry surface: ``get(name) -> AgentConfig | None``."""

        def get(self, name: str) -> AgentConfig | None: ...


def build_agent_conflict_resolver(
    *,
    policy_bundle: PolicyBundle,
    registry: _SupportsAgentLookup,
    display: ParallelDisplay,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps | None = None,
    workspace_scope: WorkspaceScope | None = None,
    display_context: DisplayContext | None = None,
) -> ConflictResolver:
    """Build the pipeline-backed conflict resolver for the integration step.

    The returned callable matches
    :data:`~ralph.pipeline.auto_integrate_resolve.ConflictResolver`: it is
    invoked with ``(repo_root, target_branch)`` while the conflicted merge
    is in progress and returns ``True`` only when Ralph's own
    deterministic marker scan found the conflicts resolved.

    Args:
        policy_bundle: Resolved policy supplying the resolution drain.
        registry: Agent registry. Used to decline BEFORE a session is
            started when no agent of the resolution chain is actually
            installed in this workspace.
        display: Active display; the pipeline pushes its own status bar
            through it and restores the previous one on exit.
        config: Run configuration, supplying the wall-clock ceiling.
        pipeline_deps: Pipeline dependencies. Required: without them no
            MCP session can be built.
        workspace_scope: Workspace scope. Required, for the same reason.
        display_context: Display context, when there is one.

    Returns:
        The resolver callable. It never raises.
    """

    def _resolver(root: Path, target: str) -> bool:
        if pipeline_deps is None or workspace_scope is None:
            missing = "pipeline_deps" if pipeline_deps is None else "workspace_scope"
            logger.warning(
                "auto_integrate: conflict resolution unavailable ({} not "
                "threaded to this seam); declining rather than invoking an "
                "agent without a Ralph MCP session",
                missing,
            )
            return False
        if not _any_chain_agent_installed(policy_bundle, registry):
            logger.warning(
                "auto_integrate: no agent of the rebase-conflict-resolution "
                "chain is installed; declining to resolve"
            )
            return False
        try:
            return run_conflict_resolution_pipeline(
                root=root,
                target=target,
                config=config,
                pipeline_deps=pipeline_deps,
                workspace_scope=workspace_scope,
                policy_bundle=policy_bundle,
                display=display,
                display_context=display_context,
            )
        except Exception as exc:
            # The pipeline contains its own failures; this is the outer
            # net that keeps the promise the integration step relies on:
            # a conflict resolver never raises into it, it only declines.
            logger.warning(
                "auto_integrate: conflict-resolution pipeline raised: {}", exc
            )
            return False

    return _resolver


def _any_chain_agent_installed(
    policy_bundle: PolicyBundle, registry: _SupportsAgentLookup
) -> bool:
    """Whether at least one resolution-chain agent exists in the registry.

    Checked here rather than inside the pipeline so a workspace with no
    usable agent never pays for a status-bar push, a prompt render and a
    session bridge before finding out.
    """
    return any(
        registry.get(agent_name) is not None
        for agent_name in resolution_chain_agents(policy_bundle)
    )
