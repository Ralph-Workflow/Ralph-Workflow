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

from ralph.agents.display_capabilities import DisplayCapability, all_display_capabilities
from ralph.agents.display_capability_stance import DisplayCapabilityStance
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
        is_builtin: Whether this is a built-in agent (catalog rejects
            custom registrations under built-in names).
        no_default_session_flag: When True, the agent opts out of the
            default ``--resume {}`` session template that would otherwise
            be applied for interactive agents.  Set by agy.
        session_identifier_observable: S-6 (Evidence Provenance G6 / DoD 20).
            Whether this transport's documented output can ever carry an
            observable session/conversation identifier at all -- a
            structural property of the transport's wire protocol, distinct
            from ``no_default_session_flag`` (which is about a CLI
            ``--resume``-style flag, not about whether an identifier is
            ever emitted). AGY has ``no_default_session_flag=True`` (no
            ``--resume`` flag) but DOES emit an observable identifier (its
            JSON ``init`` frame's ``conversation_id``), so it is NOT
            exempted from the smoke gate's "session ID was not observed"
            check. Nanocoder is the one documented ``False`` case: its
            design doc (``docs/superpowers/specs/2026-06-07-nanocoder-support-design.md``)
            found no documented unattended ``run``-mode session/resume
            output of any kind during upstream documentation review, and
            its parser (plain-text PTY redraw, no JSON session protocol)
            confirms there is no mechanism to observe one. Defaults to
            ``True`` so every other built-in and every custom agent is
            held to the same bar unless it declares otherwise.
        display_capabilities: Per-capability tri-state stance; the
            mapping's keys must match
            :func:`ralph.agents.display_capabilities.all_display_capabilities`
            exactly (no extras, no missing). Custom agents that omit the
            field get an empty mapping; the built-in spec enforcement in
            :meth:`AgentSupport.from_registration_kwargs` requires a
            complete declaration whenever ``is_builtin=True`` so the
            gate that audits built-in declarations cannot route around
            the contract.
    """

    name: str
    spec: AgentSpec
    parser_factory: Callable[[], AgentParser]
    strategy_factory: StrategyFactory
    config: AgentConfig
    is_builtin: bool = False
    no_default_session_flag: bool = False
    session_identifier_observable: bool = True
    display_capabilities: tuple[DisplayCapabilityStance, ...] = ()

    _name_lower: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "_name_lower", self.name.lower())
        _validate_display_capabilities(self.display_capabilities, is_builtin=self.is_builtin)

    @property
    def cmd(self) -> str:
        return self.config.cmd

    @property
    def transport(self) -> AgentTransport:
        return self.spec.transport

    def capability(self, capability: object) -> DisplayCapabilityStance | None:
        """Return the declared stance for ``capability``, or ``None``.

        The lookup uses the capability enum value as the key (which is
        the canonical ``SurfaceSpec.name``) so callers do not need to
        import the capability enum directly.
        """
        if isinstance(capability, DisplayCapability):
            value: str = capability.value
        else:
            if not isinstance(capability, str):
                return None
            value = capability
        for stance in self.display_capabilities:
            if isinstance(stance, DisplayCapabilityStance) and stance.capability.value == value:
                return stance
        return None

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
        no_default_session_flag: bool = False,
        session_identifier_observable: bool = True,
        display_capabilities: tuple[DisplayCapabilityStance, ...] = (),
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
            interactive: When True and ``session_flag`` is not provided and
                ``no_default_session_flag`` is False, sets a default
                ``--resume {}`` template.
            cmd: Executable command; defaults to ``name``.
            output_flag: Optional output format flag.
            yolo_flag: Optional autonomous flag string.
            verbose_flag: Optional verbose flag string.
            can_commit: Whether the agent can run git commit.
            model_flag: Optional model/provider flag.
            print_flag: Optional print flag.
            streaming_flag: Optional streaming flag.
            session_flag: Optional session continuation flag template.
            display_name: Human-readable display name.
            subagent_capability: Whether the agent exposes usable sub-agent tooling.
            is_builtin: When True, the agent is a built-in (catalog allows
                the registration under reserved built-in names).
            no_default_session_flag: When True, suppress the default
                ``--resume {}`` template.  Replaces the historical hidden
                ``name != "agy"`` special case; agy sets this to True.
            session_identifier_observable: S-6 (G6 / DoD 20). Whether this
                transport's output can ever carry an observable session
                identifier at all. Defaults to True; nanocoder is the one
                documented False case (see :class:`AgentSupport`'s
                docstring).
            display_capabilities: Per-capability stance tuple; built-in
                agents (is_builtin=True) must declare exactly one stance
                per catalog-derived capability. Custom agents may omit
                the field (empty tuple). The validation rejects empty
                reasons for non-SUPPORTED stances and rejects duplicate
                or out-of-vocabulary capabilities.

        Returns:
            An AgentSupport instance ready for AgentCatalog.add().
        """
        effective_session_flag = session_flag
        if effective_session_flag is None and interactive and not no_default_session_flag:
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
            no_default_session_flag=no_default_session_flag,
        )

        support = cls(
            name=name,
            spec=spec,
            parser_factory=parser_factory,
            strategy_factory=strategy_factory,
            config=config,
            is_builtin=is_builtin,
            no_default_session_flag=no_default_session_flag,
            session_identifier_observable=session_identifier_observable,
            display_capabilities=display_capabilities,
        )
        object.__setattr__(config, "_support", support)
        return support


def _validate_display_capabilities(
    stances: tuple[DisplayCapabilityStance, ...],
    *,
    is_builtin: bool,
) -> None:
    """Reject duplicates, missing capabilities, and reasonless non-support stances.

    Built-in agents (is_builtin=True) must declare exactly one stance per
    catalog-derived capability: nothing extra, nothing missing. Custom
    agents (is_builtin=False) are permitted to declare any subset; the
    empty tuple is the documented default for custom agents that predate
    the capability contract. Reasons for ``NOT_APPLICABLE`` and
    ``UNIMPLEMENTED`` must be non-empty strings; ``SUPPORTED`` may carry
    an optional detail. Importing the capability module here is
    intentional: the validation runs at dataclass construction time so a
    built-in shipped with a missing stance fails closed at first import
    rather than at first smoke run.
    """
    required = tuple(all_display_capabilities())
    if is_builtin:
        if not stances:
            msg = (
                "Built-in AgentSupport must declare display_capabilities "
                "covering every catalog-derived capability; got an empty tuple"
            )
            raise ValueError(msg)
        seen: set[str] = set()
        for stance in stances:
            if stance.capability in seen:
                msg = (
                    f"Duplicate display_capabilities entry for "
                    f"{stance.capability.name!r}; built-in agents must declare "
                    f"exactly one stance per capability"
                )
                raise ValueError(msg)
            seen.add(stance.capability)
        declared = {stance.capability for stance in stances}
        missing = [c for c in required if c not in declared]
        if missing:
            names = ", ".join(repr(c.name) for c in missing)
            msg = (
                f"Built-in AgentSupport is missing display_capabilities for "
                f"{names}; every catalog-derived capability must be declared"
            )
            raise ValueError(msg)
        extra = declared - set(required)
        if extra:
            names = ", ".join(repr(c.name) for c in extra)
            msg = (
                f"Built-in AgentSupport carries display_capabilities not in "
                f"the catalog-derived vocabulary: {names}"
            )
            raise ValueError(msg)
    else:
        for stance in stances:
            if stance.capability not in set(required):
                msg = (
                    f"Custom-agent display_capabilities entry "
                    f"{stance.capability!r} is not in the catalog-derived vocabulary"
                )
                raise ValueError(msg)
        seen_custom: set[str] = set()
        for stance in stances:
            if stance.capability in seen_custom:
                msg = (
                    f"Duplicate display_capabilities entry for "
                    f"{stance.capability.name!r}; each capability must appear at most once"
                )
                raise ValueError(msg)
            seen_custom.add(stance.capability)
