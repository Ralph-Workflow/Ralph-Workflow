"""Single declarative source for the 7 built-in agent declarations.

The :class:`BuiltinAgentSpec` dataclass mirrors the kwargs accepted by
:func:`ralph.agents.registration.register_agent_support` and the legacy
:class:`AgentSupport.from_registration_kwargs` so the 7 built-in entries
in :mod:`ralph.agents.builtin` can be expressed as a single declarative
row per agent, instead of repeating the kwargs across seven function calls.

Use :meth:`BuiltinAgentSpec.to_support` to materialize the dataclass into
an :class:`AgentSupport` instance.  The resulting ``is_builtin`` flag is
always ``True`` so the catalog can treat these entries as reserved.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ralph.agents.support import AgentSupport
from ralph.config.enums import AgentTransport, JsonParserType

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents._contracts import StrategyFactory
    from ralph.agents.display_capability_stance import DisplayCapabilityStance
    from ralph.agents.parsers.base import AgentParser


# Fields that are NOT forwarded to ``AgentSupport.from_registration_kwargs``
# via **kwargs because they are passed as positional / named arguments by
# :meth:`BuiltinAgentSpec.to_support`.  Kept as a frozenset so the surface
# is explicit and future built-in agent additions do not silently break
# the 7-entry frozen contract pinned by
# ``test_agents/test_builtin_spec_consolidation.py``.
_BUILTIN_SPEC_POSITIONAL_FIELDS: frozenset[str] = frozenset(
    {"transport", "parser_factory", "strategy_factory"}
)


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
        session_identifier_observable: S-6 (G6 / DoD 20). Whether this
            transport's output can ever carry an observable session
            identifier. Defaults to True; nanocoder is the one documented
            False case (see :class:`ralph.agents.support.AgentSupport`'s
            docstring for the full reasoning and its evidence source).
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
    session_identifier_observable: bool = True
    display_capabilities: tuple[DisplayCapabilityStance, ...] = ()

    def to_support(self, name: str) -> AgentSupport:
        """Materialize the dataclass into an :class:`AgentSupport`.

        Forwards every dataclass field as a keyword argument to
        :meth:`AgentSupport.from_registration_kwargs` so future
        BuiltinAgentSpec additions do not require updating two parallel
        kwarg lists.  ``is_builtin=True`` is always set.

        Args:
            name: Agent name to assign to the resulting support.

        Returns:
            The materialized :class:`AgentSupport` with ``is_builtin=True``.
        """
        asdict_result: dict[str, object] = cast(
            "dict[str, object]",
            dataclasses.asdict(self),
        )
        kwargs: dict[str, object] = {
            field: value
            for field, value in asdict_result.items()
            if field not in _BUILTIN_SPEC_POSITIONAL_FIELDS
        }
        # ``dataclasses.asdict`` recursively converts nested dataclasses
        # to plain dicts, which would lose the ``DisplayCapabilityStance``
        # class identity the downstream validator relies on. Pass the
        # original tuple through explicitly so the validator sees real
        # ``DisplayCapabilityStance`` instances.
        kwargs["display_capabilities"] = self.display_capabilities
        return AgentSupport.from_registration_kwargs(
            name,
            transport=self.transport,
            parser_factory=self.parser_factory,
            strategy_factory=self.strategy_factory,
            is_builtin=True,
            **kwargs,  # type: ignore[arg-type]  # reason: autogenerated code has no type support, see docs/agents/type-ignore-policy.md#autogenerated-code
        )


def vision_verdict_agent_spec() -> BuiltinAgentSpec:
    """Return the canonical :class:`BuiltinAgentSpec` for the vision-verdict agent.

    The vision-verdict agent (see
    :file:`ralph/agents/content/vision-verdict-agent.md`) is a
    conditional built-in: it is registered as ``is_builtin=True``
    so the catalog treats ``vision-verdict`` as a reserved name,
    but it is NOT part of the always-seeded
    :data:`ralph.agents.builtin._BUILTIN_AGENT_SUPPORTS` tuple.
    The :func:`ralph.agents.vision_agent_provisioning.provision_vision_verdict_agent`
    call site is the only place that materializes the spec into a
    support; it does so when the design-system policy is in scope
    for the active workspace.

    The factory lives in :mod:`ralph.agents.builtin_spec` so the
    canonical kwarg surface for every built-in agent is in one
    place, even when the agent is conditionally provisioned. The
    three display capabilities are declared
    :meth:`~ralph.agents.display_capability_stance.DisplayCapabilityStance.not_applicable`
    because the agent emits a
    :class:`~ralph.visual.design_verdict.DesignVerdict` artifact
    rather than a TUI surface; declaring NOT_APPLICABLE is the
    honest stance, not UNIMPLEMENTED.

    Returns:
        A fresh :class:`BuiltinAgentSpec` carrying the
        vision-verdict kwargs. The spec is independent of the
        always-seeded built-in tuple so the conditional
        provisioning can be exercised without leaking the agent
        into the always-on path.
    """
    from ralph.agents.display_capabilities import (
        DisplayCapability,  # reason: lazy import keeps the module-level dependency graph clean
    )
    from ralph.agents.display_capability_stance import (
        DisplayCapabilityStance,  # reason: same lazy-import rationale
    )
    from ralph.agents.execution_state.generic_execution_strategy import (  # reason: same lazy-import rationale
        GenericExecutionStrategy,
    )
    from ralph.agents.parsers.generic import (
        GenericParser,  # reason: same lazy-import rationale
    )
    from ralph.config.enums import (  # reason: same lazy-import rationale
        AgentTransport,
        JsonParserType,
    )

    display_capabilities: tuple[DisplayCapabilityStance, ...] = (
        DisplayCapabilityStance.not_applicable(
            DisplayCapability.SYNTAX_HIGHLIGHTING,
            reason=(
                "vision-verdict is an in-process vision judge; "
                "it does not produce a TUI surface"
            ),
        ),
        DisplayCapabilityStance.not_applicable(
            DisplayCapability.FILE_PREVIEW,
            reason=(
                "vision-verdict emits a DesignVerdict artifact, not a "
                "file preview"
            ),
        ),
        DisplayCapabilityStance.not_applicable(
            DisplayCapability.EDIT_DIFF,
            reason=(
                "vision-verdict does not edit files; its judgement is "
                "captured in the verdict artifact, not a diff surface"
            ),
        ),
    )
    return BuiltinAgentSpec(
        transport=AgentTransport.GENERIC,
        parser_factory=GenericParser,
        strategy_factory=GenericExecutionStrategy,
        json_parser=JsonParserType.GENERIC,
        cmd="vision-verdict",
        can_commit=False,
        display_name="Vision Verdict",
        display_capabilities=display_capabilities,
    )
