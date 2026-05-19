from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.config.models import AgentConfig


class _FakeRegistryInstance:
    """Minimal AgentRegistry instance stub that always returns a fixed config."""

    def __init__(self, agent_config: AgentConfig) -> None:
        self._agent_config = agent_config

    def get(self, name: str) -> AgentConfig | None:
        del name
        return self._agent_config
