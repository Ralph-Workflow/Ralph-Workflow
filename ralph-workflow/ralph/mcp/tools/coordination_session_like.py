"""Protocol for coordination tool session access."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CoordinationSessionLike(Protocol):
    """Minimum session surface required by coordination handlers.

    Sessions may optionally carry a ``broker_secret`` (RFC-013 P3) — a
    broker-owned string the agent never sees directly, used to HMAC
    the run-scoped receipt and completion sentinel so a model with
    workspace write capabilities cannot forge either. ``None`` means
    no HMAC enforcement at write or read time (the pre-P3 contract);
    the handlers downcast gracefully when the attribute is absent.
    """

    session_id: str
    run_id: str

    @property
    def broker_secret(self) -> str | None:
        """RFC-013 P3 broker-owned HMAC secret. Read-only at the
        protocol surface; the implementation decides whether the
        value is supplied by a constructor arg, a property backed by
        one, or a dataclass field with a ``None`` default."""
        ...

    def check_capability(self, capability: str) -> object:
        """Return a policy outcome for the requested capability."""

    #: Optional :class:`ralph.mcp.explore.handlers.ExploreIndex`
    #: handle attached by the production session bridge. The
    #: protocol stays permissive (``object | None``) so legacy
    #: sessions that never opted into indexed exploration remain
    #: compatible without an explicit cast.
    explore_index: object | None
