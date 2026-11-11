
from __future__ import annotations

from typing import Protocol


class SessionBridgeLike(Protocol):

    @property
    def run_id(self) -> str:
        ...

    def start(self) -> None:
        ...

    def agent_endpoint_uri(self) -> str:
        ...

    def endpoint_uri(self) -> str:
        ...

    def shutdown(self) -> None:
        ...


__all__ = ["SessionBridgeLike"]
