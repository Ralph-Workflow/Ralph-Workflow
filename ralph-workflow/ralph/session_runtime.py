"""Public managed agent-session runtime for Ralph-hosted mini workflows.

Exposes a small, reusable runtime seam for tools that need Ralph to supervise a
constrained agent session without entering the full policy-driven pipeline.
Callers own the higher-level host loop; Ralph owns the MCP bridge, agent
invocation wiring, resumable session environment, and optional master-prompt
materialization.

Contract:

- The runtime is created via :class:`ManagedAgentSessionRuntime.open` (a
  classmethod) which constructs an :class:`AgentSession`, starts a session
  bridge, optionally materializes a master prompt, and returns the runtime.
  Constructor failures shut down any bridge that was already started so no
  half-initialized MCP listener is left behind.
- All collaborators (workspace factory, MCP server starter, agent invoker,
  master prompt materializer, MCP bridge shutdown) are injectable through
  :class:`ManagedAgentSessionDeps` so the runtime is black-box testable
  without real subprocesses or filesystem operations.
- :class:`ManagedAgentSessionRequest` is a frozen dataclass that carries the
  caller-supplied inputs (session id prefix, drain, optional capabilities,
  optional pre-resolved :class:`SessionMcpPlan`, optional master-prompt
  name). It is the canonical request shape a host loop passes in.
- The runtime manages the lifecycle of its MCP bridge via :meth:`close`
  (also wired through the context-manager protocol). Callers are responsible
  for invoking :meth:`close` (or using ``with``) when the host loop is done.
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
