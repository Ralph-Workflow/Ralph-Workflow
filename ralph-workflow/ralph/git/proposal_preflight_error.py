"""Exception for proposal preflight verification failures."""

from __future__ import annotations

from ralph.git.proposal_seal_error import ProposalSealError


class ProposalPreflightError(ProposalSealError):
    """Raised when preflight verification of HEAD or proposal ref fails."""
