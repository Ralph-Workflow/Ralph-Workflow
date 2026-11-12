from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity


@dataclass(frozen=True)
class SessionModelOpts:
    model_identity: MultimodalModelIdentity | None = None
    model_flag: str | None = None
