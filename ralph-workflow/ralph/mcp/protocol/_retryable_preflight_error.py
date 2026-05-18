"""Transient preflight error that may succeed if retried."""

from __future__ import annotations

from ralph.mcp.protocol._preflight_error import PreflightError


class RetryablePreflightError(PreflightError):
    """Transient preflight errors that may succeed if retried."""
