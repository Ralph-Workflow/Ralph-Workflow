from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.multimodal.capabilities import (
            MultimodalModelIdentity,
            ResolvedCapabilityProfile,
        )

class _SessionContract:
    """Bundled session contract parameters to reduce argument count."""

    drain: str = ""
    capabilities: frozenset[str] = frozenset()
    model_identity: MultimodalModelIdentity | None = None
    capability_profile: ResolvedCapabilityProfile | None = None
