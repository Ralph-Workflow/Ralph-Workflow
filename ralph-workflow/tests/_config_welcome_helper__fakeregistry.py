from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tests.test_config_welcome import _FakeAgent


class _FakeRegistry:
    """Fake agent registry for testing availability checks.

    Implements list_agents() -> list[str] and get(name) -> _FakeAgent | None
    to match the _HasListAgents protocol used by emit_first_run_welcome.
    """

    def __init__(self, agents: dict[str, _FakeAgent]) -> None:
        self._agents = agents

    def list_agents(self) -> list[str]:
        return list(self._agents.keys())

    def get(self, name: str) -> _FakeAgent | None:
        return self._agents.get(name)
