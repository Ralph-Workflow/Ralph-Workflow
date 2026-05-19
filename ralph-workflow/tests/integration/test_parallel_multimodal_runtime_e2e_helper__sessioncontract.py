from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity


class _SessionContract(NamedTuple):
    """Session contract parameters for parallel worker testing."""

    drain: str
    capabilities: frozenset[str]
    model_identity: MultimodalModelIdentity
