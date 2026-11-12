from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CoordinationSessionLike(Protocol):
    session_id: str
    run_id: str

    @property
    def broker_secret(self) -> str | None: ...

    def check_capability(self, capability: str) -> object:
        pass

    explore_index: object | None
