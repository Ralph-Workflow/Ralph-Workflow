"""Agent registry for managing available AI agents.

The registry loads agent configurations and resolves agent names
to their executable commands and settings.
"""

from __future__ import annotations

import shlex
from copy import deepcopy
from typing import TYPE_CHECKING

from loguru import logger

from ralph.config.ccs_config import CcsAliasConfig, CcsConfig
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig

_MIN_OPENCODE_SEGMENTS = 2
_MIN_NANOCODER_PROVIDER_SEGMENTS = 2
_MIN_AGY_SEGMENTS = 2
_CLAUDE_MODEL_SEGMENTS = 2

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig


def builtin_agents() -> dict[str, AgentConfig]:
    """Return the built-in agent configurations keyed by agent name."""
    return {
        # Interactive Claude runs inside Ralph Workflow's MCP boundary, so we
        # bypass Claude's own approval prompts here and rely on the Ralph
        # Workflow MCP/tool allowlist to remain the permission control layer.
        "claude": AgentConfig(
            cmd="claude",
            output_flag=None,
            yolo_flag="--dangerously-skip-permissions",
            verbose_flag="--verbose",
            can_commit=True,
            json_parser=JsonParserType.CLAUDE,
            session_flag="--resume {}",
            transport=AgentTransport.CLAUDE_INTERACTIVE,
        ),
        "claude-headless": AgentConfig(
            cmd="claude -p",
            output_flag="--output-format=stream-json",
            yolo_flag="--permission-mode auto",
            verbose_flag="--verbose",
            can_commit=True,
            json_parser=JsonParserType.CLAUDE,
            print_flag="--print",
            streaming_flag="--include-partial-messages",
            session_flag="--resume {}",
            transport=AgentTransport.CLAUDE,
        ),
        "codex": AgentConfig(
            cmd="codex exec",
            output_flag="--json",
            yolo_flag="--dangerously-bypass-approvals-and-sandbox",
            can_commit=True,
            json_parser=JsonParserType.CODEX,
            transport=AgentTransport.CODEX,
        ),
        "opencode": AgentConfig(
            cmd="opencode",
            output_flag="--json-stream",
            can_commit=False,
            json_parser=JsonParserType.OPENCODE,
            # opencode run --session <id> resumes an existing session
            session_flag="--session {}",
            transport=AgentTransport.OPENCODE,
        ),
        "nanocoder": AgentConfig(
            cmd="nanocoder",
            output_flag=None,
            can_commit=False,
            json_parser=JsonParserType.GENERIC,
            transport=AgentTransport.NANOCODER,
        ),
        "agy": AgentConfig(
            cmd="agy",
            output_flag=None,
            yolo_flag="--dangerously-skip-permissions",
            print_flag="--print",
            can_commit=False,
            json_parser=JsonParserType.GENERIC,
            transport=AgentTransport.AGY,
        ),
    }


class AgentRegistry:
    """Registry of available AI agents.

    The registry maintains a mapping of agent names to their configurations.
    It supports loading agents from UnifiedConfig and resolving agent
    names at runtime.

    Attributes:
        agents: Dictionary mapping agent names to their configurations.
    """

    def __init__(self, *, ccs_defaults: CcsConfig | None = None) -> None:
        """Initialize an empty agent registry."""
        self.agents: dict[str, AgentConfig] = {}
        self._ccs_defaults = ccs_defaults or CcsConfig()

    @classmethod
    def from_config(cls, config: UnifiedConfig) -> AgentRegistry:
        """Create registry from UnifiedConfig.

        Args:
            config: Unified configuration containing agent definitions.

        Returns:
            Populated AgentRegistry instance.
        """
        registry = cls(ccs_defaults=config.ccs)

        for name, agent_config in builtin_agents().items():
            registry.register(name, agent_config)

        for name, agent_config in config.agents.items():
            registry.register(name, agent_config)

        for alias, alias_value in config.ccs_aliases.items():
            registry.register(f"ccs/{alias}", _resolve_ccs_alias(alias_value, config.ccs))

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
        config = self.agents.get(name)
        if config is not None:
            return config
        return _resolve_dynamic_agent(name, self._ccs_defaults)

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
            allowed_no_output = (
                AgentTransport.CLAUDE_INTERACTIVE,
                AgentTransport.NANOCODER,
                AgentTransport.AGY,
            )
            if config.transport not in allowed_no_output and not config.output_flag:
                errors.append(f"Agent '{name}' has no output flag configured")
        return errors


def _resolve_ccs_alias(alias_value: str | CcsAliasConfig, defaults: CcsConfig) -> AgentConfig:
    if isinstance(alias_value, str):
        return AgentConfig(
            cmd=alias_value,
            output_flag=defaults.output_flag,
            yolo_flag=defaults.yolo_flag,
            verbose_flag=defaults.verbose_flag,
            can_commit=defaults.can_commit,
            json_parser=JsonParserType(defaults.json_parser),
            print_flag=defaults.print_flag,
            streaming_flag=defaults.streaming_flag,
            session_flag=defaults.session_flag,
            transport=AgentTransport.CLAUDE,
        )

    parser = (
        JsonParserType(alias_value.json_parser)
        if alias_value.json_parser
        else JsonParserType(defaults.json_parser)
    )

    return AgentConfig(
        cmd=alias_value.cmd,
        output_flag=alias_value.output_flag or defaults.output_flag,
        yolo_flag=alias_value.yolo_flag
        if alias_value.yolo_flag is not None
        else defaults.yolo_flag,
        verbose_flag=(
            alias_value.verbose_flag
            if alias_value.verbose_flag is not None
            else defaults.verbose_flag
        ),
        can_commit=alias_value.can_commit
        if alias_value.can_commit is not None
        else defaults.can_commit,
        json_parser=parser,
        model_flag=alias_value.model_flag,
        print_flag=alias_value.print_flag
        if alias_value.print_flag is not None
        else defaults.print_flag,
        streaming_flag=(
            alias_value.streaming_flag
            if alias_value.streaming_flag is not None
            else defaults.streaming_flag
        ),
        session_flag=alias_value.session_flag
        if alias_value.session_flag is not None
        else defaults.session_flag,
        transport=AgentTransport.CLAUDE,
    )


def _resolve_dynamic_agent(name: str, ccs_defaults: CcsConfig) -> AgentConfig | None:
    segments = name.split("/")
    resolved: AgentConfig | None = None

    if name.startswith("opencode/"):
        if len(segments) < _MIN_OPENCODE_SEGMENTS or not all(segments[1:]):
            return None

        base_config = deepcopy(builtin_agents()["opencode"])
        dynamic_overrides: dict[str, object] = {
            "model_flag": f"-m {_normalize_opencode_model_id(name)}",
            "can_commit": True,
        }
        resolved = base_config.model_copy(update=dynamic_overrides)
    elif name.startswith("nanocoder/"):
        if len(segments) < _MIN_NANOCODER_PROVIDER_SEGMENTS or not all(segments[1:]):
            return None

        base_config = deepcopy(builtin_agents()["nanocoder"])
        provider, model = _normalize_nanocoder_provider_and_model(name)
        model_flag = f"--provider {shlex.quote(provider)}"
        if model is not None:
            model_flag += f" --model {shlex.quote(model)}"
        nanocoder_overrides: dict[str, object] = {"model_flag": model_flag, "can_commit": True}
        resolved = base_config.model_copy(update=nanocoder_overrides)
    elif name.startswith("agy/"):
        if len(segments) < _MIN_AGY_SEGMENTS or not segments[1]:
            return None

        base_config = deepcopy(builtin_agents()["agy"])
        # AGY model IDs from `agy models` are display names and may contain
        # spaces/parentheses (e.g. "Claude Sonnet 4.6 (Thinking)"). Quote the
        # value so shlex.split in the command builder keeps it as one argument.
        agy_overrides: dict[str, object] = {
            "model_flag": f"--model {shlex.quote(segments[1])}",
            "can_commit": True,
        }
        resolved = base_config.model_copy(update=agy_overrides)
    elif len(segments) == _CLAUDE_MODEL_SEGMENTS and segments[1]:
        if name.startswith("ccs/"):
            resolved = _resolve_dynamic_ccs_agent(name, ccs_defaults)
        elif name.startswith("claude-headless/"):
            base_config = deepcopy(builtin_agents()["claude-headless"])
            claude_headless_overrides: dict[str, object] = {"model_flag": f"--model {segments[1]}"}
            resolved = base_config.model_copy(update=claude_headless_overrides)
        elif name.startswith("claude/"):
            base_config = deepcopy(builtin_agents()["claude"])
            claude_overrides: dict[str, object] = {"model_flag": f"--model {segments[1]}"}
            resolved = base_config.model_copy(update=claude_overrides)

    return resolved


def _resolve_dynamic_ccs_agent(name: str, ccs_defaults: CcsConfig) -> AgentConfig | None:
    segments = name.split("/")
    if len(segments) != _CLAUDE_MODEL_SEGMENTS or not segments[1]:
        return None
    return _resolve_ccs_alias(f"ccs {segments[1]}", ccs_defaults)


def _normalize_opencode_model_id(name: str) -> str:
    return name.removeprefix("opencode/")


def _normalize_nanocoder_provider_and_model(name: str) -> tuple[str, str | None]:
    parts = name.removeprefix("nanocoder/").split("/")
    provider = parts[0]
    model = "/".join(parts[1:]) if len(parts) > 1 else None
    return provider, model
