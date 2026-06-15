"""SessionBridgeLike Protocol for MCP session bridge abstraction."""

from __future__ import annotations

from typing import Protocol


class SessionBridgeLike(Protocol):
    """Protocol describing the session bridge interface used here."""

    @property
    def run_id(self) -> str:
        """The session's run identity (the receipt key for the completion gate).

        The submission handler stamps receipts with this run_id, and the
        completion gate looks them up with the same value. Anything that
        derives an MCP_RUN_ID_ENV or otherwise references "which run?" MUST
        read this property — never an independent label — so the receipt and
        the gate cannot disagree about run identity.
        """
        ...

    def start(self) -> None:
        """Start accepting MCP connections."""
        ...

    def agent_endpoint_uri(self) -> str:
        """Return the agent-facing endpoint URI."""
        ...

    def endpoint_uri(self) -> str:
        """Return the raw endpoint URI used for transport-level preflight."""
        ...

    def shutdown(self) -> None:
        """Shut down the bridge."""
        ...


__all__ = ["SessionBridgeLike"]
