"""Per-transport multimodal status contract (criterion 16).

Closed per-transport vocabulary that decides which transports are
covered (round-trip graded + perceptible delivery required), which
are excluded with a named reason, and which carry the negative
``no-MCP`` contract ``GENERIC`` holds. The contract is the production
companion to ``tests/test_transport_multimodal_status.py``: every
:class:`ralph.config.agent_transport.AgentTransport` value must have a
declared, observable multimodal status. Adding a new transport without
declaring its status is a registration error caught at import time
(see :func:`multimodal_status`).

Three states:

- :attr:`MultimodalStatus.COVERED` -- round-trip graded + perceptible
  delivery required. Every non-``GENERIC`` transport currently lands
  here.
- :attr:`MultimodalStatus.EXCLUDED` -- reserved for a future transport
  the project has a named reason to decline.
- :attr:`MultimodalStatus.NO_MCP` -- the transport carries no MCP
  transport by design (``GENERIC``).

Production callers import :func:`multimodal_status`; tests in
``tests/test_transport_multimodal_status.py`` keep the closed vocabulary
pinned against the AgentTransport enum so a tenth transport cannot
silently inherit "unaccounted".
"""

from __future__ import annotations

from enum import StrEnum

from ralph.config.agent_transport import AgentTransport


class MultimodalStatus(StrEnum):
    """Closed per-transport multimodal status vocabulary."""

    COVERED = "covered"
    EXCLUDED = "excluded"
    NO_MCP = "no_mcp"


# Per-transport explicit multimodal status. ``GENERIC`` carries the
# negative contract (no MCP by design); ``CODEX`` and ``PI`` are
# declared covered at criterion 16 (criterion 5 widens their smoke
# surface). Every non-GENERIC transport is COVERED by criterion 16.
_TRANSPORT_STATUS: dict[AgentTransport, MultimodalStatus] = {
    AgentTransport.CLAUDE: MultimodalStatus.COVERED,
    AgentTransport.CLAUDE_INTERACTIVE: MultimodalStatus.COVERED,
    AgentTransport.CODEX: MultimodalStatus.COVERED,
    AgentTransport.OPENCODE: MultimodalStatus.COVERED,
    AgentTransport.NANOCODER: MultimodalStatus.COVERED,
    AgentTransport.AGY: MultimodalStatus.COVERED,
    AgentTransport.PI: MultimodalStatus.COVERED,
    AgentTransport.CURSOR: MultimodalStatus.COVERED,
    AgentTransport.GENERIC: MultimodalStatus.NO_MCP,
}


def multimodal_status(transport: AgentTransport) -> MultimodalStatus:
    """Return the multimodal status for ``transport``.

    Raises ``KeyError`` for an undocumented transport. The
    closed-vocabulary contract ensures callers can switch on the
    returned status without spelling drift.
    """
    return _TRANSPORT_STATUS[transport]


def all_transport_status_pairs() -> tuple[tuple[AgentTransport, MultimodalStatus], ...]:
    """Return all transport/status pairs in deterministic order."""
    return tuple((t, _TRANSPORT_STATUS[t]) for t in sorted(_TRANSPORT_STATUS, key=str))


__all__ = [
    "MultimodalStatus",
    "all_transport_status_pairs",
    "multimodal_status",
]
