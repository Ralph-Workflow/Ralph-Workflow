"""AuditParseError exception for audit_kwargs_forwarding."""

from __future__ import annotations


class AuditParseError(Exception):
    """A file under audit could not be parsed, so it could not be cleared."""
