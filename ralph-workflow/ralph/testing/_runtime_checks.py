from __future__ import annotations

__all__ = ["require"]


def require(condition: object, message: str = "") -> None:
    if not condition:
        raise AssertionError(message)
