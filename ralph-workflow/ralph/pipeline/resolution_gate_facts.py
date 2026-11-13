from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResolutionGateFacts:
    stored_key: tuple[str | None, tuple[str, ...], str | None] | None
    stop_identity: str | None
    eligible_agents: tuple[str, ...]
    config_fingerprint: str | None
    observed_at_ms: int
