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
    del config
    return REFRESH_LOCAL_FLEET if observe_target_sha(root, target) is not None else REFRESH_DISABLED
