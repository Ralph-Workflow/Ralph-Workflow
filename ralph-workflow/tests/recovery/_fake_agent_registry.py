from __future__ import annotations


class _FakeAgentRegistry:
    """Minimal fake registry for test injection."""

    def __init__(self, known_agents: set[str]) -> None:
        self._known = known_agents

    def get(self, name: str) -> object | None:
        return object() if name in self._known else None
