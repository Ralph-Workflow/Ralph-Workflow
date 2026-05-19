from __future__ import annotations


class _SessionContract:
    def __init__(
        self,
        *,
        drain: str,
        capabilities: frozenset[str],
        model_identity: object,
        capability_profile: object,
    ) -> None:
        self.drain = drain
        self.capabilities = capabilities
        self.model_identity = model_identity
        self.capability_profile = capability_profile
