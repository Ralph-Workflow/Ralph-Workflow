"""Typed failure for standalone MCP activity-relay delivery."""

from __future__ import annotations


class ActivityRelayError(RuntimeError):
    """A typed failure in the conflict-resolution liveness relay."""


__all__ = ["ActivityRelayError"]
