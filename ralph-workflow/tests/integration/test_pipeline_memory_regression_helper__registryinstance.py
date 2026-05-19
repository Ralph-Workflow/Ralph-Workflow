from __future__ import annotations

from ralph.config.enums import JsonParserType
from ralph.config.models import AgentConfig


class _RegistryInstance:
    def get(self, name: str) -> AgentConfig | None:
        del name
        return AgentConfig(
            cmd="generic-agent",
            output_flag="--json-stream",
            json_parser=JsonParserType.GENERIC,
        )
