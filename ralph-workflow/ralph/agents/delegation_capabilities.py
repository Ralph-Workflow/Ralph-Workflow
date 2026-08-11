"""Per-transport sub-agent / task delegation declaration for Ralph Workflow agents.

Documents which :class:`ralph.config.agent_transport.AgentTransport` values
can spawn a sub-agent during a Ralph Workflow run, the mechanism the
upstream runtime uses, and a citation pointing at the in-repo evidence
that grounds the declaration.

The declaration mirrors the S-4 tri-state ``DisplayCapabilityStance``
pattern from :mod:`ralph.agents.display_capability_stance` -- the same
fail-closed vocabulary, but applied to sub-agent / task delegation rather
than to display-surface rendering. The mapping is the canonical source
of truth for downstream callers (parallelization planner, effect-router
fallback policy, prompt-template guidance) that need to branch on
whether the executing agent can dispatch its own parallel workers.

Adding a new :class:`AgentTransport` member without adding a matching
:class:`DelegationCapability` is a hard error: the import-time
completeness check in this module fails closed when a transport is
declared but not declared-on. The check is enforced at module import
time (not at first use) so a missing entry is surfaced immediately,
not at the first invocation that happens to ask the question.

All citations are repo-relative paths to the file and line range
where the evidence lives, so they can be navigated and verified with
a single ``read_file`` call. URLs from external upstream
documentation are recorded when they appear in the cited repo file.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ralph.config.agent_transport import AgentTransport


class DelegationStance(StrEnum):
    """Tri-state stance on sub-agent / task delegation for one transport.

    Attributes:
        SUPPORTED: The upstream runtime exposes usable sub-agent tooling
            that Ralph can dispatch through. The accompanying
            :attr:`DelegationCapability.mechanism` describes HOW.
        EXPLICIT_UNSUPPORTED: The upstream runtime either has no
            sub-agent tooling, or -- in the headless invocation mode
            Ralph uses -- does not document a stable way for Ralph to
            dispatch one. Distinct from :attr:`NOT_APPLICABLE` because
            the transport is a real agent runtime that COULD in
            principle support delegation, just not in a way Ralph can
            exercise today.
        NOT_APPLICABLE: This transport is a generic / fallback carrier
            with no runtime semantics of its own; the question ``can
            this transport spawn a subagent?`` has no defined answer
            because there is no real runtime to delegate to.
    """

    SUPPORTED = "supported"
    EXPLICIT_UNSUPPORTED = "explicit_unsupported"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class DelegationCapability:
    """One transport's declaration of sub-agent / task delegation.

    Attributes:
        transport: The :class:`AgentTransport` this declaration covers.
        stance: The tri-state :class:`DelegationStance`.
        mechanism: Free-form description of the mechanism the upstream
            runtime uses (e.g. ``"Native Task tool (renamed Agent in
            claude v2.1.63)"``). Empty string when the stance is
            :attr:`DelegationStance.NOT_APPLICABLE`.
        citation: Repo-relative path or canonical evidence citation
            (e.g. ``"ralph-workflow/ralph/mcp/tools/names.py:201-209"``)
            grounding the declaration. Must be non-empty so every
            declaration carries a pointer at the evidence the entry is
            derived from.
    """

    transport: AgentTransport
    stance: DelegationStance
    mechanism: str
    citation: str


def _build_claude() -> DelegationCapability:
    """Claude (headless) delegation declaration."""
    return DelegationCapability(
        transport=AgentTransport.CLAUDE,
        stance=DelegationStance.SUPPORTED,
        mechanism=(
            "Native Task/Agent tool (renamed in claude v2.1.63); "
            "auto-allowed on the headless JSON-stream transport via "
            "CLAUDE_NATIVE_TOOLS_TO_KEEP"
        ),
        citation=(
            "ralph-workflow/ralph/mcp/tools/names.py:201-209; "
            "ralph-workflow/ralph/config/agent_config.py:86-91"
        ),
    )


def _build_claude_interactive() -> DelegationCapability:
    """Claude-interactive (PTY) delegation declaration."""
    return DelegationCapability(
        transport=AgentTransport.CLAUDE_INTERACTIVE,
        stance=DelegationStance.SUPPORTED,
        mechanism=(
            "Native Task/Agent tool (renamed in claude v2.1.63); "
            "auto-allowed on the interactive PTY transport via "
            "CLAUDE_NATIVE_TOOLS_TO_KEEP"
        ),
        citation=(
            "ralph-workflow/ralph/mcp/tools/names.py:201-209; "
            "ralph-workflow/ralph/config/agent_config.py:86-91"
        ),
    )


def _build_codex() -> DelegationCapability:
    """Codex delegation declaration."""
    return DelegationCapability(
        transport=AgentTransport.CODEX,
        stance=DelegationStance.SUPPORTED,
        mechanism=(
            "features.multi_agent = true set explicitly via "
            "CODEX_NATIVE_FEATURE_OVERRIDES"
        ),
        citation="ralph-workflow/ralph/mcp/tools/names.py:213-221",
    )


def _build_opencode() -> DelegationCapability:
    """OpenCode delegation declaration."""
    return DelegationCapability(
        transport=AgentTransport.OPENCODE,
        stance=DelegationStance.SUPPORTED,
        mechanism=(
            "Native 'task' tool auto-allowed via OPENCODE_NATIVE_TOOLS_TO_KEEP; "
            "subagent PIDs surfaced via stdout child lifecycle events "
            "(child_started with a pid field)"
        ),
        citation=(
            "ralph-workflow/ralph/mcp/tools/names.py:189-197; "
            "ralph-workflow/ralph/process/monitor/documentation-sources.md:31-36"
        ),
    )


def _build_nanocoder() -> DelegationCapability:
    """Nanocoder delegation declaration."""
    return DelegationCapability(
        transport=AgentTransport.NANOCODER,
        stance=DelegationStance.SUPPORTED,
        mechanism=(
            "Upstream daemon-based subagent support per nanocoder docs; "
            "no stable per-subagent CLI token for external observers"
        ),
        citation="ralph-workflow/ralph/process/monitor/documentation-sources.md:99-104",
    )


def _build_agy() -> DelegationCapability:
    """AGY (Google Antigravity) delegation declaration."""
    return DelegationCapability(
        transport=AgentTransport.AGY,
        stance=DelegationStance.EXPLICIT_UNSUPPORTED,
        mechanism=(
            "Interactive /agents panel only; headless invocation requires "
            "user-configured sub-agents and is unverified on stock installs "
            "(effect_router gates dispatch on 'agy agents' availability)"
        ),
        citation=(
            "ralph-workflow/ralph/process/monitor/documentation-sources.md:56-63; "
            "ralph-workflow/ralph/pipeline/effect_router.py:135-143"
        ),
    )


def _build_pi() -> DelegationCapability:
    """Pi (pi.dev) delegation declaration."""
    return DelegationCapability(
        transport=AgentTransport.PI,
        stance=DelegationStance.EXPLICIT_UNSUPPORTED,
        mechanism=(
            "No documented sub-agent dispatch surface; the Pi transport "
            "wires MCP through a generated extension only"
        ),
        citation=(
            "ralph-workflow/ralph/config/agent_transport.py (PI docstring); "
            "ralph-workflow/ralph/mcp/transport/pi.py"
        ),
    )


def _build_cursor() -> DelegationCapability:
    """Cursor Agent CLI delegation declaration."""
    return DelegationCapability(
        transport=AgentTransport.CURSOR,
        stance=DelegationStance.SUPPORTED,
        mechanism=(
            "Built-in sub-agent / task tooling per Cursor Agent CLI docs; "
            "grouped with Claude Code / OpenCode / Codex / AGY in mcp.toml"
        ),
        citation="ralph-workflow/ralph/policy/defaults/mcp.toml:132-134",
    )


def _build_generic() -> DelegationCapability:
    """Generic (fallback) transport delegation declaration."""
    return DelegationCapability(
        transport=AgentTransport.GENERIC,
        stance=DelegationStance.NOT_APPLICABLE,
        mechanism="",
        citation=(
            "ralph-workflow/ralph/config/agent_transport.py "
            "(GENERIC docstring: 'No special transport support')"
        ),
    )


#: Frozen tuple of all declarations, one per :class:`AgentTransport`.
#: Ordered by the enum declaration order in
#: :mod:`ralph.config.agent_transport` so the tuple is reproducible.
_DELEGATION_CAPABILITIES: tuple[DelegationCapability, ...] = (
    _build_claude(),
    _build_claude_interactive(),
    _build_codex(),
    _build_opencode(),
    _build_nanocoder(),
    _build_agy(),
    _build_pi(),
    _build_cursor(),
    _build_generic(),
)


def _build_by_transport() -> dict[AgentTransport, DelegationCapability]:
    """Index :data:`_DELEGATION_CAPABILITIES` by transport, asserting 1:1."""
    out: dict[AgentTransport, DelegationCapability] = {}
    for entry in _DELEGATION_CAPABILITIES:
        if entry.transport in out:
            msg = (
                f"Duplicate DelegationCapability for transport "
                f"{entry.transport!r}; each transport must have exactly "
                f"one declaration in _DELEGATION_CAPABILITIES"
            )
            raise RuntimeError(msg)
        out[entry.transport] = entry
    return out


def _assert_complete_against_enum(
    by_transport: dict[AgentTransport, DelegationCapability],
) -> None:
    """Import-time invariant: every AgentTransport member has an entry.

    A future enum addition is a hard error at import time rather than a
    silent ``KeyError`` at first lookup. The check runs at module
    import so a regression surfaces immediately, not at the first
    invocation that happens to ask the question.
    """
    declared = set(by_transport.keys())
    expected = set(AgentTransport)
    missing = expected - declared
    extra = declared - expected
    if missing or extra:
        msg_parts: list[str] = []
        if missing:
            missing_list = ", ".join(sorted(repr(t) for t in missing))
            msg_parts.append(f"missing entries for transports: {missing_list}")
        if extra:
            extra_list = ", ".join(sorted(repr(t) for t in extra))
            msg_parts.append(f"unknown transports declared: {extra_list}")
        msg = (
            "DelegationCapability coverage does not match AgentTransport "
            "membership: " + "; ".join(msg_parts)
        )
        raise RuntimeError(msg)


_BY_TRANSPORT: dict[AgentTransport, DelegationCapability] = _build_by_transport()
_assert_complete_against_enum(_BY_TRANSPORT)


def delegation_for(transport: AgentTransport) -> DelegationCapability:
    """Return the delegation declaration for ``transport``.

    Every :class:`AgentTransport` member is guaranteed an entry by the
    import-time completeness check, so this lookup never raises
    :class:`KeyError` for a documented transport.
    """
    return _BY_TRANSPORT[transport]


def all_delegation_capabilities() -> tuple[DelegationCapability, ...]:
    """Return the frozen tuple of all declared :class:`DelegationCapability` entries."""
    return _DELEGATION_CAPABILITIES


__all__ = [
    "DelegationCapability",
    "DelegationStance",
    "all_delegation_capabilities",
    "delegation_for",
]
