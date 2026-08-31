"""RebaseVerificationError -- raised when verifying rebase completion fails."""

from __future__ import annotations


class RebaseVerificationError(Exception):
    """Raised when verifying rebase completion fails."""


__all__ = ["RebaseVerificationError"]
