"""AgentSessionId — convenience wrapper around a session identifier."""

from __future__ import annotations


class AgentSessionId:
    """Convenience wrapper around a session identifier."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    @classmethod
    def from_string(cls, value: str) -> AgentSessionId:
        return cls(value)

    def as_str(self) -> str:
        return self._value

    def __str__(self) -> str:
        return self._value


__all__ = ["AgentSessionId"]
