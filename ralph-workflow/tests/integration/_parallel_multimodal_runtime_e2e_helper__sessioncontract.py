from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity


class _SessionContract(NamedTuple):
    drain: str
    capabilities: frozenset[str]
    model_identity: MultimodalModelIdentity
