"""Mock session with media manifest for workspace media tool tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    MultimodalModelIdentity,
    ResolvedCapabilityProfile,
    profile_for_caller,
)
from ralph.mcp.multimodal.resources import MediaManifest


@dataclass
class MockSessionWithManifest:
    """Stand-in session exposing the surface the media tools actually read.

    The ``caller_*`` accessors are NOT decoration. Real sessions resolve
    media delivery through them, and a mock that omitted them sent every
    media test down the fallback branch instead -- so guards on the
    production path could be deleted with the suite still green.
    """

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
        delegated = self.delegated_model_identity
        if delegated is None:
            return self.model_identity
        from ralph.mcp.multimodal.capabilities import identity_on_transport

        return identity_on_transport(delegated, self.model_identity.transport)

    @property
    def caller_capability_profile(self) -> ResolvedCapabilityProfile:
        stored = (
            self.delegated_capability_profile
            if self.delegated_capability_profile is not None
            else (None if self.delegated_model_identity is not None else self.capability_profile)
        )
        return profile_for_caller(stored, self.caller_model_identity)

    def check_capability(self, capability: str) -> object:
        return capability == self.allowed_capability

    def check_edit_area(self, path: str) -> object:
        return True
