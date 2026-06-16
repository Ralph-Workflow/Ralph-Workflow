"""Single declarative source for the 6 built-in agent declarations.

The :class:`BuiltinAgentSpec` dataclass mirrors the kwargs accepted by
:func:`ralph.agents.registration.register_agent_support` and the legacy
:class:`AgentSupport.from_registration_kwargs` so the 6 built-in entries
in :mod:`ralph.agents.builtin` can be expressed as a single declarative
row per agent, instead of repeating the kwargs across six function calls.

Use :meth:`BuiltinAgentSpec.to_support` to materialize the dataclass into
an :class:`AgentSupport` instance.  The resulting ``is_builtin`` flag is
always ``True`` so the catalog can treat these entries as reserved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.agents.support import AgentSupport
from ralph.config.enums import AgentTransport, JsonParserType

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents._contracts import StrategyFactory
    from ralph.agents.parsers.base import AgentParser


@dataclass(frozen=True, slots=True)
class BuiltinAgentSpec:
    """Declarative description of one built-in agent.

    Attributes:
        transport: Transport enum value.
        parser_factory: Callable returning a parser instance.
        strategy_factory: Callable returning an execution strategy instance.
        json_parser: Parser type token.
        cmd: Executable command; defaults to ``name`` on materialization.
        output_flag: Optional output format flag.
        yolo_flag: Optional autonomous flag string.
        verbose_flag: Optional verbose flag string.
        can_commit: Whether the agent can run git commit.
        model_flag: Optional model/provider flag.
        print_flag: Optional print flag.
        streaming_flag: Optional streaming flag.
        session_flag: Optional session continuation flag template.
        display_name: Human-readable display name.
        interactive: Whether the agent is interactive (PTY).
        subagent_capability: Whether the agent exposes usable sub-agent tooling.
        no_default_session_flag: When True, suppress the default
            ``--resume {}`` session template that would otherwise be set
            by :meth:`AgentSupport.from_registration_kwargs` for
            interactive agents.  Used for agy.
    """

    transport: AgentTransport
    parser_factory: Callable[[], AgentParser]
    strategy_factory: StrategyFactory
    json_parser: JsonParserType = JsonParserType.GENERIC
    cmd: str | None = None
    output_flag: str | None = None
    yolo_flag: str | None = None
    verbose_flag: str | None = None
    can_commit: bool = False
    model_flag: str | None = None
    print_flag: str | None = None
    streaming_flag: str | None = None
    session_flag: str | None = None
    display_name: str | None = None
    interactive: bool = False
    subagent_capability: bool | None = None
    no_default_session_flag: bool = False

    def to_support(self, name: str) -> AgentSupport:
        """Materialize the dataclass into an :class:`AgentSupport`.

        Args:
            name: Agent name to assign to the resulting support.

        Returns:
            The materialized :class:`AgentSupport` with ``is_builtin=True``.
        """
        return AgentSupport.from_registration_kwargs(
            name,
            cmd=self.cmd,
            output_flag=self.output_flag,
            yolo_flag=self.yolo_flag,
            verbose_flag=self.verbose_flag,
            can_commit=self.can_commit,
            json_parser=self.json_parser,
            model_flag=self.model_flag,
            print_flag=self.print_flag,
            streaming_flag=self.streaming_flag,
            session_flag=self.session_flag,
            display_name=self.display_name,
            transport=self.transport,
            parser_factory=self.parser_factory,
            strategy_factory=self.strategy_factory,
            interactive=self.interactive,
            subagent_capability=self.subagent_capability,
            is_builtin=True,
            no_default_session_flag=self.no_default_session_flag,
        )
