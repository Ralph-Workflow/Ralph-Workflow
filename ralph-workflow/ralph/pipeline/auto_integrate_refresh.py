"""Local-fleet freshness observation for auto-integration.

The remote-sync seam performs configured-remote checks; this helper re-observes
only the shared local target when remote synchronization is disabled or absent.
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

    Remote fetching belongs exclusively to the configured remote-sync seam.
    Local integration still re-reads the shared branch ref so sibling
    worktrees remain visible when remote sync is disabled or unavailable.
    """
    del config
    return REFRESH_LOCAL_FLEET if observe_target_sha(root, target) is not None else REFRESH_DISABLED
