"""Strict ingestion error for the ``RALPH-EVIDENCE:`` ledger parser.

Single-class module per the repository's class-per-module policy.
"""

from __future__ import annotations


class EvidenceParseError(ValueError):
    """A ``RALPH-EVIDENCE:`` line is malformed; the ledger cannot be trusted."""


__all__ = ["EvidenceParseError"]
