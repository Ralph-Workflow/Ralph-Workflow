from __future__ import annotations

from dataclasses import dataclass

from ralph.recovery.wait_kind import WaitKind

__all__ = ("EligibilityWait",)


@dataclass(frozen=True)
class EligibilityWait:
    kind: WaitKind
    wait_ms: int | None
    until_ms: int | None = None
    reason: str | None = None

    @property
    def is_available_now(self) -> bool:
        return self.kind is WaitKind.AVAILABLE_NOW

    @property
    def is_never_eligible(self) -> bool:
        return self.kind is WaitKind.NEVER_ELIGIBLE
