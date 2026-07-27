"""Local freshness observation for auto-integration.

Remote fetches occur only through the opt-in remote-sync path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.auto_integrate_sync import (
    REFRESH_DISABLED,
    REFRESH_LOCAL_FLEET,
    observe_target_sha,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig

__all__ = ["refresh_target"]


def refresh_target(config: UnifiedConfig, root: Path, target: str) -> str:
    """Re-observe the local target without contacting a remote.

    Remote fetching belongs exclusively to the opt-in remote-sync path.
    Local integration still re-reads the shared branch ref so sibling
    worktrees remain visible when remote sync is disabled.
    """
    del config
    return REFRESH_LOCAL_FLEET if observe_target_sha(root, target) is not None else REFRESH_DISABLED
