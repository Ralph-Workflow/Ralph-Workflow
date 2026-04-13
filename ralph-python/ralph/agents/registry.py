"""Agent registry for managing available AI agents.

The registry loads agent configurations and resolves agent names
to their executable commands and settings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from ralph.config.models import AgentConfig, UnifiedConfig


class AgentRegistry:
    """Registry of available AI agents.

    The registry maintains a mapping of agent names to their configurations.
    It supports loading agents from UnifiedConfig and resolving agent
    names at runtime.

    Attributes:
        agents: Dictionary mapping agent names to their configurations.
    """

    def __init__(self) -> None:
        """Initialize an empty agent registry."""
        self.agents: dict[str, AgentConfig] = {}

    @classmethod
    def from_config(cls, config: UnifiedConfig) -> AgentRegistry:
        """Create registry from UnifiedConfig.

        Args:
            config: Unified configuration containing agent definitions.

        Returns:
            Populated AgentRegistry instance.
        """
        registry = cls()
        for name, agent_config in config.agents.items():
            registry.register(name, agent_config)
        logger.debug("Loaded {} agents from config", len(registry.agents))
        return registry

    def register(self, name: str, config: AgentConfig) -> None:
        """Register an agent with the registry.

        Args:
            name: Agent name.
            config: Agent configuration.
        """
        self.agents[name] = config
        logger.debug("Registered agent: {}", name)

    def get(self, name: str) -> AgentConfig | None:
        """Get agent configuration by name.

        Args:
            name: Agent name.

        Returns:
            AgentConfig if found, None otherwise.
        """
        return self.agents.get(name)

    def list_agents(self) -> list[str]:
        """List all registered agent names.

        Returns:
            List of agent names.
        """
        return list(self.agents.keys())

    def get_command(self, name: str) -> str | None:
        """Get the command for an agent.

        Args:
            name: Agent name.

        Returns:
            Command string if agent found, None otherwise.
        """
        config = self.get(name)
        return config.cmd if config else None

    def validate(self) -> list[str]:
        """Validate all registered agents.

        Returns:
            List of validation error messages (empty if all valid).
        """
        errors: list[str] = []
        for name, config in self.agents.items():
            if not config.cmd:
                errors.append(f"Agent '{name}' has no command configured")
            if not config.output_flag:
                errors.append(f"Agent '{name}' has no output flag configured")
        return errors
