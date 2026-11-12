from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.protocol._access_denied_code import AccessDeniedCode


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str | None = None
    code: AccessDeniedCode | None = None

    @classmethod
    def allow(cls) -> AccessDecision:
        return cls(allowed=True)

    @classmethod
    def deny(cls, reason: str, code: AccessDeniedCode) -> AccessDecision:
        return cls(allowed=False, reason=reason, code=code)

    def is_allowed(self) -> bool:
        return self.allowed


__all__ = ["AccessDecision"]
