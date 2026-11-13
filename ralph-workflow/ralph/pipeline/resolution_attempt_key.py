from __future__ import annotations

from typing import NamedTuple


class ResolutionAttemptKey(NamedTuple):
    stop_identity: str | None
    eligible_agents: tuple[str, ...]
    config_fingerprint: str | None
