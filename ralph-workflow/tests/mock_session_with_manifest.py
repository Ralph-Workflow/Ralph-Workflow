from __future__ import annotations

from dataclasses import dataclass, field

from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    MultimodalModelIdentity,
    ResolvedCapabilityProfile,
    caller_identity_for,
    caller_profile_for,
)
from ralph.mcp.multimodal.resources import MediaManifest


@dataclass
class MockSessionWithManifest:
    allowed_capability: str | None = None
    session_id: str = "test-session"
    run_id: str = "test-run"
    broker_secret: str | None = None
    media_manifest: MediaManifest = field(default_factory=MediaManifest)
    model_identity: MultimodalModelIdentity = field(default=UNKNOWN_IDENTITY)
    capability_profile: ResolvedCapabilityProfile | None = None
    delegated_model_identity: MultimodalModelIdentity | None = None
    delegated_capability_profile: ResolvedCapabilityProfile | None = None

    @property
    def caller_model_identity(self) -> MultimodalModelIdentity:
        return caller_identity_for(self.model_identity, self.delegated_model_identity)

    @property
    def caller_capability_profile(self) -> ResolvedCapabilityProfile:
        return caller_profile_for(
            self.model_identity,
            self.delegated_model_identity,
            self.capability_profile,
            self.delegated_capability_profile,
        )

    def check_capability(self, capability: str) -> object:
        return capability == self.allowed_capability

    def check_edit_area(self, path: str) -> object:
        return True
