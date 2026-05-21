"""Public managed agent-session runtime for Ralph-hosted mini workflows.

This module exposes a small, reusable runtime seam for tools that need Ralph to
supervise a constrained agent session without entering the full policy-driven
pipeline. Callers own the higher-level host loop while Ralph owns the MCP bridge,
agent invocation wiring, resumable session environment, and optional system-prompt
materialization.
"""

from __future__ import annotations

from ._session_runtime_deps import ManagedAgentSessionDeps
from ._session_runtime_request import ManagedAgentSessionRequest
from ._session_runtime_runtime import ManagedAgentSessionRuntime

__all__ = [
    "ManagedAgentSessionDeps",
    "ManagedAgentSessionRequest",
    "ManagedAgentSessionRuntime",
]
