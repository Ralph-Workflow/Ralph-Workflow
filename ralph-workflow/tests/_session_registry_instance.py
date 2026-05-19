from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.config.models import AgentConfig


class _RegistryInstance:
    def __init__(self, agent_config: AgentConfig) -> None:
        self._agent_config = agent_config

    def get(self, name: str) -> AgentConfig | None:
        del name
        return self._agent_config
