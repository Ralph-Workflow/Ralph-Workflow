"""Typed errors raised by the workspace-context switch.

The workspace-context seam (:mod:`ralph.workspace.context`) turns every
configuration failure it observes into one typed error, so callers react to
a single contract: the offending file, the reason, and the remediation
command. The error lives in its own module so a caller can catch it without
importing the context manager (and, through it, the config/policy/MCP chain
the context manager resolves lazily).
"""

from __future__ import annotations

from pathlib import Path


class WorkspaceContextError(Exception):
    """Raised when entering a workspace context fails due to invalid configuration."""

    def __init__(self, file: Path, reason: str, fix: str | None = None) -> None:
        self.file = Path(file)
        self.reason = reason
        self.fix = fix or f"fix {self.file} then run ralph --check-config"
        if self.fix in self.reason:
            super().__init__(self.reason)
        else:
            super().__init__(f"{self.reason} ({self.fix})")


__all__ = ["WorkspaceContextError"]
