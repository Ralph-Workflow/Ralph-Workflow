
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _ResolvedEnv:

    no_color: bool
    force_color: bool
    columns: int | None
    force_ascii: bool
    streaming_dedup_enabled: bool
    streaming_checkpoints_enabled: bool
