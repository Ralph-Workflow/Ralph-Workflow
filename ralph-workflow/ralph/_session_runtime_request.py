
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.session_plan import SessionMcpPlan


@dataclass(frozen=True)
class ManagedAgentSessionRequest:

    session_id_prefix: str
    drain: str
    capabilities: frozenset[str] | None = None
    session_mcp_plan: SessionMcpPlan | None = None
    server_env: dict[str, str] | None = None
    master_prompt_name: str | None = None
    default_product_criteria: str | None = None


__all__ = ["ManagedAgentSessionRequest"]
