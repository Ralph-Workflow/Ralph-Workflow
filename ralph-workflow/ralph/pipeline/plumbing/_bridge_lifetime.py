"""Shared bridge lifetime manager for plumbing commands.

This module is the single owner of per-chain bridge startup and shutdown.
Both :func:`ralph.pipeline.plumbing.commit_plumbing.run_commit_plumbing` and
:func:`ralph.pipeline.plumbing.smoke_plumbing.run_smoke_plumbing` route their
bridge construction through :func:`with_bridge_lifetime` so the try/finally
shutdown logic cannot drift between the two plumbing paths.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from ralph.mcp.server.lifecycle import SessionBridgeLike
    from ralph.pipeline.factory import PipelineCore
    from ralph.pipeline.session_bridge import BridgeFactory
    from ralph.policy.models import AgentsPolicy


@contextmanager
def with_bridge_lifetime(
    pipeline_core: PipelineCore,
    bridge_factory: BridgeFactory,
    *,
    repo_root: Path,
    drain: str,
    session_id_prefix: str,
    agents_policy: AgentsPolicy | None = None,
    run_id: str | None = None,
) -> Iterator[SessionBridgeLike]:
    """Own bridge startup/shutdown for a single plumbing chain or smoke run.

    Yields the bridge created from ``bridge_factory`` and guarantees
    ``bridge.shutdown()`` is called in the ``finally`` block, even when the
    body raises.
    """
    if run_id is None:
        bridge = bridge_factory(
            workspace_root=repo_root,
            drain=drain,
            agents_policy=agents_policy,
            session_id_prefix=session_id_prefix,
            model_identity=pipeline_core.model_identity,
        )
    else:
        bridge = bridge_factory(
            workspace_root=repo_root,
            drain=drain,
            agents_policy=agents_policy,
            session_id_prefix=session_id_prefix,
            run_id=run_id,
            model_identity=pipeline_core.model_identity,
        )
    try:
        yield bridge
    finally:
        bridge.shutdown()


__all__ = ["with_bridge_lifetime"]
