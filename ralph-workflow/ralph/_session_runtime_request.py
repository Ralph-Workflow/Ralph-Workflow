"""Public request model for Ralph-managed standalone agent sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.session_plan import SessionMcpPlan


@dataclass(frozen=True)
class ManagedAgentSessionRequest:
    """Caller-supplied inputs that shape one Ralph-managed standalone agent session.

    This frozen dataclass is the canonical contract that host loops (e.g. the
    Ralph pipeline, ad-hoc prompt runners, or external tooling) pass to
    :meth:`ralph.session_runtime.ManagedAgentSessionRuntime.open` to describe
    one isolated agent session. It is intentionally a value object: every
    field is immutable and there is no behavior, so two requests that compare
    equal produce identical session lifecycles.

    Attributes:
        session_id_prefix: Short, human-meaningful prefix prepended to the
            generated session id (e.g. ``"plan"`` or ``"verify"``). The
            runtime appends ``-<uuid4_hex[:8]>`` to produce the unique
            session id. The prefix surfaces in log lines and checkpoint
            files, so prefer lowercase, stable identifiers.
        drain: Phase-style label that names the kind of work the session is
            performing (``"planning"``, ``"execution"``, ``"review"``,
            ``"verification"``, ...). ``drain`` flows into
            :class:`ralph.mcp.protocol.session.AgentSession.drain`, governs
            which capabilities are exposed through the MCP bridge, and is
            used by :func:`ralph.mcp.protocol.startup.access_mode_for_drain`
            to choose read-only vs read/write tool access.
        capabilities: Optional explicit set of MCP-bridge capability names to
            expose. When ``None`` the runtime resolves capabilities from the
            configured ``AgentsPolicy`` via
            :func:`ralph.mcp.session_plan.build_session_mcp_plan`. Pass an
            explicit value when the caller needs to lock capabilities for
            testing or for hardened isolation modes.
        session_mcp_plan: Optional pre-resolved :class:`SessionMcpPlan` that
            fully describes the session's MCP capabilities, model identity,
            and server-side environment. When supplied, ``capabilities`` and
            ``server_env`` are ignored and this plan is used verbatim. Useful
            for hosts that resolve plans ahead of time (e.g. for caching or
            cross-session reuse).
        server_env: Optional environment variables to merge into the MCP
            server subprocess environment (in addition to Ralph's defaults).
            Reserved names (``MCP_ENDPOINT``, ``MCP_RUN_ID``,
            ``AGENT_LABEL_SCOPE``) are managed by the runtime and cannot be
            overridden here.
        system_prompt_name: Optional name of a system-prompt template to
            materialize for the session. When ``None`` the agent is invoked
            without an explicit system prompt. The materializer writes the
            resolved file under the workspace and returns its path.
        default_current_prompt: Optional fallback path used when the chosen
            system-prompt template references a ``current`` placeholder that
            has no other source. Has no effect when ``system_prompt_name`` is
            ``None``.

    Invariants:
        - The dataclass is frozen; mutating an instance raises
          :class:`dataclasses.FrozenInstanceError`.
        - Every field is optional except ``session_id_prefix`` and ``drain``;
          the runtime treats the others as overrides or precomputed hints.
        - Fields are not used directly by the runtime after
          :meth:`ManagedAgentSessionRuntime.open` returns; the resolved
          :class:`AgentSession` carries the immutable view of the session.

    Example:
        >>> request = ManagedAgentSessionRequest(
        ...     session_id_prefix="plan",
        ...     drain="planning",
        ...     capabilities=frozenset({"read_repo", "list_artifacts"}),
        ...     system_prompt_name="planning/default",
        ...     default_current_prompt="your-prompt-file.md",
        ... )
    """

    session_id_prefix: str
    drain: str
    capabilities: frozenset[str] | None = None
    session_mcp_plan: SessionMcpPlan | None = None
    server_env: dict[str, str] | None = None
    system_prompt_name: str | None = None
    default_current_prompt: str | None = None


__all__ = ["ManagedAgentSessionRequest"]
