from __future__ import annotations

from typing import TYPE_CHECKING

from tests._session_registry_instance import _RegistryInstance

if TYPE_CHECKING:
    from ralph.config.models import AgentConfig, UnifiedConfig


class _RegistryFactory:
    _agent_config: AgentConfig

    @classmethod
    def from_config(cls, config: UnifiedConfig) -> _RegistryInstance:
        del config
        return _RegistryInstance(cls._agent_config)
