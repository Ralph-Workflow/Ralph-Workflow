
from __future__ import annotations


class PolicyValidationError(Exception):

    def __init__(self, message: str, source: str | None = None) -> None:
        self.message = message
        self.source = source
        super().__init__(message)
