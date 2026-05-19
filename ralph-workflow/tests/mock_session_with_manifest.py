"""Mock session with media manifest for workspace media tool tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from ralph.mcp.multimodal.capabilities import UNKNOWN_IDENTITY, MultimodalModelIdentity
from ralph.mcp.multimodal.resources import MediaManifest


@dataclass
class MockSessionWithManifest:
    allowed_capability: str | None = None
    session_id: str = "test-session"
    media_manifest: MediaManifest = field(default_factory=MediaManifest)
    model_identity: MultimodalModelIdentity = field(default=UNKNOWN_IDENTITY)

    def check_capability(self, capability: str) -> object:
        return capability == self.allowed_capability

    def check_edit_area(self, path: str) -> object:
        return True
