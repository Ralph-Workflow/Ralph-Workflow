"""The single registration unit for one agent.

Replace the legacy 4-way mutation of _PARSER_REGISTRY, _CUSTOM_COMMAND_REGISTRY,
_STRATEGY_DISPATCH, and the caller's AgentRegistry with a single
AgentCatalog.add(support).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from ralph.agents.spec import AgentSpec
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport, JsonParserType

if TYPE_CHECKING:
    from ralph.agents._contracts import StrategyFactory
    from ralph.agents.parsers.base import AgentParser


@dataclass(frozen=True, slots=True)
class AgentSupport:
    """Bundles one agent's registration data.

    Attributes:
        name: Agent name (lowercased on construction).
        spec: The AgentSpec capturing headless-vs-interactive axis.
        parser_factory: Callable returning a parser instance.
        strategy_factory: Callable returning an execution strategy instance.
        config: The agent's AgentConfig.
    """

    name: str
    spec: AgentSpec
    parser_factory: Callable[[], AgentParser]
    strategy_factory: StrategyFactory
    config: AgentConfig
    is_builtin: bool = False

    _name_lower: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "_name_lower", self.name.lower())

    @property
    def cmd(self) -> str:
        return self.config.cmd

    @property
    def transport(self) -> AgentTransport:
        return self.spec.transport

    @classmethod
    def from_registration_kwargs(
        cls,
        name: str,
        *,
        transport: AgentTransport,
        parser_factory: Callable[[], AgentParser],
        strategy_factory: StrategyFactory,
        agent_registry: object = None,
        json_parser: JsonParserType = JsonParserType.GENERIC,
        interactive: bool = False,
        cmd: str | None = None,
        output_flag: str | None = None,
        yolo_flag: str | None = None,
        verbose_flag: str | None = None,
        can_commit: bool = False,
        model_flag: str | None = None,
        print_flag: str | None = None,
        streaming_flag: str | None = None,
        session_flag: str | None = None,
        display_name: str | None = None,
        subagent_capability: bool | None = None,
        is_builtin: bool = False,
    ) -> AgentSupport:
        """Build an AgentSupport from the legacy register_agent_support kwargs.

        Args:
            name: Agent name.
            transport: Transport enum value.
            parser_factory: Callable returning a parser instance.
            strategy_factory: Callable returning an execution strategy instance.
            agent_registry: Accepted for signature compatibility with the legacy
                ``register_agent_support`` API; unused inside this method.
            json_parser: Parser type token.
            interactive: When True and ``session_flag`` is not provided, sets a
                default resume template.
            cmd: Executable command; defaults to ``name``.
            output_flag: Optional output format flag.
            yolo_flag: Optional autonomous flag string.
            verbose_flag: Optional verbose flag string.
            can_commit: Whether the agent can run git commit.
            model_flag: Optional model/provider flag.
            print_flag: Optional print flag.
            streaming_flag: Optional streaming flag.
            session_flag: Optional session continuation flag template.
                When ``interactive=True`` and this is omitted, a
                ``--resume {}`` template is used.
            display_name: Human-readable display name.
            subagent_capability: Whether the agent exposes usable sub-agent tooling.

        Returns:
            An AgentSupport instance ready for AgentCatalog.add().
        """
        effective_session_flag = session_flag
        if effective_session_flag is None and interactive and name != "agy":
            effective_session_flag = "--resume {}"

        config = AgentConfig(
            cmd=cmd if cmd is not None else name,
            output_flag=output_flag,
            yolo_flag=yolo_flag,
            verbose_flag=verbose_flag,
            can_commit=can_commit,
            json_parser=json_parser,
            model_flag=model_flag,
            print_flag=print_flag,
            streaming_flag=streaming_flag,
            session_flag=effective_session_flag,
            display_name=display_name,
            transport=transport,
            subagent_capability=subagent_capability,
        )

        spec = AgentSpec.from_agent_config(
            config,
            interactive=interactive,
            completion_required=bool(effective_session_flag),
        )

        support = cls(
            name=name,
            spec=spec,
            parser_factory=parser_factory,
            strategy_factory=strategy_factory,
            config=config,
            is_builtin=is_builtin,
        )
        object.__setattr__(config, "_support", support)
        return support
