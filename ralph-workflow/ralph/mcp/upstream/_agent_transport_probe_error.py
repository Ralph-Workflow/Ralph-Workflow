"""Probe error for agent transport configuration."""

from __future__ import annotations


class AgentTransportProbeError(RuntimeError):
    """Raised when the synthesized agent config payload is malformed."""
