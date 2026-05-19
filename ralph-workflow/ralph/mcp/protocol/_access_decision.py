"""AccessDecision — result of an MCP access decision."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.protocol._access_denied_code import AccessDeniedCode


@dataclass(frozen=True)
class AccessDecision:
    """Result of an MCP access decision."""

    allowed: bool
    reason: str | None = None
    code: AccessDeniedCode | None = None

    @classmethod
    def allow(cls) -> AccessDecision:
        """Build an allow decision."""
        return cls(allowed=True)

    @classmethod
    def deny(cls, reason: str, code: AccessDeniedCode) -> AccessDecision:
        """Build a deny decision."""
        return cls(allowed=False, reason=reason, code=code)

    def is_allowed(self) -> bool:
        """Return whether access is allowed."""
        return self.allowed


__all__ = ["AccessDecision"]
