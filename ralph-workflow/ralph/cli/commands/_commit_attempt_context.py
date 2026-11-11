from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import GeneralConfig, UnifiedConfig
    from ralph.mcp.server.lifecycle import SessionBridgeLike


@dataclass(frozen=True)
class CommitAttemptContext:

    repo_root: Path
    verbose: bool
    extra_env: dict[str, str]
    general_config: GeneralConfig | UnifiedConfig | None = None
    bridge: SessionBridgeLike | None = None
