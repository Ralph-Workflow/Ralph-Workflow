"""Permanent preflight error that must abort the connection attempt."""

from __future__ import annotations

from ralph.mcp.protocol._preflight_error import PreflightError


class PermanentPreflightError(PreflightError):
    """Preflight errors that must abort the connection attempt."""
