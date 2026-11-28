from __future__ import annotations

from typing import Final

from ralph.verify_budget import contended_step_timeout

SNAPSHOT_VERSION_BASE_TIMEOUT_SECONDS: Final[float] = 60.0


def snapshot_version_timeout(load_share: float) -> float:
    return contended_step_timeout(SNAPSHOT_VERSION_BASE_TIMEOUT_SECONDS, load_share)


__all__ = ["SNAPSHOT_VERSION_BASE_TIMEOUT_SECONDS", "snapshot_version_timeout"]
