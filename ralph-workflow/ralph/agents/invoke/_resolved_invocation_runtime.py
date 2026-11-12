from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class ResolvedInvocationRuntime:
    agent_env: dict[str, str] | None = None
    server_env: dict[str, str] | None = None
    mcp_endpoint: str | None = None
    cleanup: Callable[[], None] | None = None
