from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class ResolvedInvocationRuntime:
    """Resolved runtime configuration for a single agent invocation.

    The optional ``cleanup`` hook is invoked by ``invoke_agent`` in its
    ``finally`` block after the agent subprocess has finished (success,
    failure, or cancellation). It is the documented release path for
    transport-specific resources allocated during ``resolve()`` — the
    primary example is the per-invocation Codex ``CODEX_HOME``
    directory allocated by ``CodexRuntimeResolver`` (see
    ``ralph.mcp.transport.codex.release_codex_home``).

    Lifetime contract:

      - ``cleanup`` MUST be safe to call exactly ONCE; ``invoke_agent``
        treats it as idempotent only via the implementation (e.g.
        ``release_codex_home`` returns ``False`` on a second call
        without raising, so it is safe even if a caller races the
        finally block).
      - ``cleanup`` MAY be ``None`` for resolvers that allocate no
        per-invocation resources (Claude, OpenCode, Nanocoder, Agy,
        Generic, Pi). The ``invoke_agent`` finally block tolerates a
        ``None`` hook.
      - ``cleanup`` is INVOKED EVEN IF THE SUBPROCESS RAISES. The
        hook is the mechanism that prevents a long-lived process from
        accumulating per-invocation temp directories (or other
        transport resources) when an agent run crashes.

    Why a callable and not a single-method protocol: the resolver
    closes over whatever transport-specific state it needs to release
    (for Codex, the ``codex_home`` path string). Encoding the
    per-invocation lifetime into a closure keeps the seam narrow
    without leaking the resource registry's mutable state into the
    public API.
    """

    agent_env: dict[str, str] | None = None
    server_env: dict[str, str] | None = None
    mcp_endpoint: str | None = None
    cleanup: Callable[[], None] | None = None
