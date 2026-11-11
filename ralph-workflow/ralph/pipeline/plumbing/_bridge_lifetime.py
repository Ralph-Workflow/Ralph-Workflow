
from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from ralph.config.enums import AgentTransport
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
    transport: AgentTransport | None = None,
    drop_injected_identity: bool = False,
) -> Iterator[SessionBridgeLike]:
    effective_identity = None if drop_injected_identity else pipeline_core.model_identity
    if run_id is None:
        bridge = bridge_factory(
            workspace_root=repo_root,
            drain=drain,
            agents_policy=agents_policy,
            session_id_prefix=session_id_prefix,
            model_identity=effective_identity,
            transport=transport,
        )
    else:
        bridge = bridge_factory(
            workspace_root=repo_root,
            drain=drain,
            agents_policy=agents_policy,
            session_id_prefix=session_id_prefix,
            run_id=run_id,
            model_identity=effective_identity,
            transport=transport,
        )
    try:
        yield bridge
    finally:
        bridge.shutdown()


__all__ = ["with_bridge_lifetime"]
