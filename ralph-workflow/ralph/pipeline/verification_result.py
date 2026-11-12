from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VerificationResult:
    ran: bool
    passed: bool | None
    exit_code: int | None
