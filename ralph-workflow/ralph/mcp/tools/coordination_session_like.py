"""Protocol for coordination tool session access."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CoordinationSessionLike(Protocol):
    """Minimum session surface required by coordination handlers."""

    session_id: str
    run_id: str

    def check_capability(self, capability: str) -> object:
        """Return a policy outcome for the requested capability."""
