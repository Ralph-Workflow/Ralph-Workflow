"""Verification result for post-fan-out serialized verification."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of the post-fan-out serialized verification run."""

    ran: bool
    passed: bool | None
    exit_code: int | None
