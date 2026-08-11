"""Switch the active agent context to a target worktree.

The :func:`workspace_context` context manager executes a switch → use →
restore round trip in one expression: the caller enters with a target
worktree's root, uses the yielded :class:`WorkspaceContext` bundle
inside the ``with`` block, and exits with the caller's resources
byte-identical to before. Nothing is mutated globally: the caller
passes no globals in, the bundle exposes only the target's own values,
and the only thing the caller does on exit is stop using the target
values that go out of scope with the ``with`` block.

The contract has three load-bearing properties:

- **Target-only bundle.** Every value on the bundle comes from the
  target worktree (``resolve_workspace_scope``,
  ``load_config(workspace_scope=...)``,
  ``load_policy_for_workspace_scope``,
  ``AgentRegistry.from_config``,
  ``resolve_effective_session_mcp_plan(target_scope.root)``). The
  caller's resources are never consulted inside the block.
- **No globals.** The context manager does not install a thread-local
  or process-global "active context" pointer. Nesting is implicit: an
  inner ``with`` overwrites nothing in the outer ``with`` because the
  outer bundle is bound to the outer frame, not to global state.
- **Restore exactly.** Every exit path -- normal completion,
  exception, nested exit, explicit ``return`` -- runs no cleanup work
  because nothing was globally mutated. The caller cannot observe any
  drift in PROMPT.md bytes, effective config, policy bundle, agent
  registry, or effective MCP plan.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.workspace.scope import WorkspaceScope, resolve_workspace_scope

if TYPE_CHECKING:
    from ralph.agents.registry import AgentRegistry
    from ralph.config.models import UnifiedConfig
    from ralph.mcp.effective_session_mcp_plan import EffectiveSessionMcpPlan
    from ralph.policy.models import PolicyBundle


@dataclass(frozen=True)
class WorkspaceContext:
    """Immutable bundle of context values bound to a target worktree.

    Every value here is the TARGET's own: the scope is the target's,
    the config is the target's, the policy is the target's, the agent
    registry is the target's, and the MCP plan is the target's. The
    caller's resources are not part of this bundle and are never
    accessed inside the ``with`` block.

    Treat instances as opaque, behavior-only values: consumers should
    call the methods on the embedded objects (``ctx.config``,
    ``ctx.target_scope``, etc.) rather than introspecting the bundle's
    internal layout. The frozen dataclass is the public contract.
    """

    target_scope: WorkspaceScope
    config: UnifiedConfig
    policy_bundle: PolicyBundle
    registry: AgentRegistry
    effective_mcp_plan: EffectiveSessionMcpPlan
    prompt_path: Path

    @property
    def root(self) -> Path:
        """The target workspace root, equivalent to ``target_scope.root``."""
        return self.target_scope.root


@contextmanager
def workspace_context(target_root: Path) -> Iterator[WorkspaceContext]:
    """Enter the active context for ``target_root`` and exit cleanly.

    The context manager validates the target, resolves every
    path-bound value for the target, and yields one immutable bundle.
    On exit (normal, exception, or nested) the caller's resources are
    unchanged because the context manager installs no global state.

    Args:
        target_root: Path to the target worktree's root. The path must
            exist and resolve to a Ralph workspace; anything else raises
            before the bundle is yielded.

    Yields:
        A :class:`WorkspaceContext` carrying the target's scope,
        config, policy, agent registry, effective MCP plan, and prompt
        path.

    Raises:
        FileNotFoundError: ``target_root`` does not exist.
        ValueError: ``target_root`` resolves to no workspace.
    """
    # Local imports: every dependency here -- config loader, policy
    # loader, agent registry, MCP plan resolver -- pulls in a chain
    # that ends in ``ralph.workspace.__init__`` being re-entered. The
    # cyclic chain is broken by doing all of the imports lazily here
    # so the package-level import of ``ralph.workspace`` stays acyclic.
    from ralph.agents.registry import AgentRegistry
    from ralph.config.loader import load_config
    from ralph.mcp.session_plan import resolve_effective_session_mcp_plan
    from ralph.policy.loader import load_policy_for_workspace_scope

    target_root = Path(target_root)
    if not target_root.exists():
        raise FileNotFoundError(f"workspace_context: target worktree does not exist: {target_root}")

    target_scope = resolve_workspace_scope(target_root)
    if not target_scope.root.exists():
        raise ValueError(
            f"workspace_context: target workspace root does not exist: {target_scope.root}"
        )

    config = load_config(workspace_scope=target_scope)
    policy_bundle = load_policy_for_workspace_scope(target_scope, config=config)
    registry = AgentRegistry.from_config(config)
    effective_mcp_plan = resolve_effective_session_mcp_plan(target_scope.root)
    prompt_path = target_scope.root / "PROMPT.md"

    yield WorkspaceContext(
        target_scope=target_scope,
        config=config,
        policy_bundle=policy_bundle,
        registry=registry,
        effective_mcp_plan=effective_mcp_plan,
        prompt_path=prompt_path,
    )


__all__ = ["WorkspaceContext", "workspace_context"]
