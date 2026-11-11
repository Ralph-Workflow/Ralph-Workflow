
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.protocol.capability_mapping import PolicyMode


@dataclass(frozen=True)
class McpAuditCorrelation:

    run_id: str | None = None
    generation: int | None = None
    drain: str | None = None
    policy_mode: PolicyMode | None = None


__all__ = ["McpAuditCorrelation"]
