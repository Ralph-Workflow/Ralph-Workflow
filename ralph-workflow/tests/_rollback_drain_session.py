from __future__ import annotations

from tests._rollback_session import _Session


class _DrainSession(_Session):
    broker_secret = None

    def __init__(self, drain: str) -> None:
        self.drain = drain
