"""Agent registry for managing available AI agents.

The registry loads agent configurations and resolves agent names
to their executable commands and settings.
"""

from __future__ import annotations

import shlex
from copy import deepcopy
from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.builtin import builtin_supports
from ralph.agents.catalog import AgentCatalog, default_catalog
from ralph.agents.registration import register_agent_support_to_catalog
from ralph.agents.spec import AgentSpec
from ralph.agents.support import AgentSupport
from ralph.config.ccs_config import CcsAliasConfig, CcsConfig
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig

if TYPE_CHECKING:
    from collections.abc import Callable

_MIN_OPENCODE_SEGMENTS = 2
_MIN_NANOCODER_PROVIDER_SEGMENTS = 2
_MIN_AGY_SEGMENTS = 2
_MIN_PI_SEGMENTS = 2
_CLAUDE_MODEL_SEGMENTS = 2

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig


def builtin_agents() -> dict[str, AgentConfig]:
    """Return the built-in agent configurations keyed by agent name."""
    return {support.name: support.config for support in builtin_supports()}


def _find_builtin_support(name: str) -> AgentSupport | None:
    """Return the built-in :class:`AgentSupport` for ``name`` or ``None``.

    Used by :meth:`AgentRegistry.register` to detect configured
    ``[agents.<name>]`` overrides for built-in agents.  Returns
    ``None`` for non-built-in names so a custom registration is
    unaffected.
    """
    for support in builtin_supports():
        if support.name == name:
            return support
    return None


def _synthesize_override_support(
    name: str,
    config: AgentConfig,
    builtin: AgentSupport,
) -> AgentSupport:
    """Build an :class:`AgentSupport` from a configured ``[agents.<name>]`` override.

    Preserves the built-in's parser factory, strategy factory, and
    the spec flags that are NOT exposed on :class:`AgentConfig`
    (``interactive``, ``no_default_session_flag``) — those are
    properties of the transport, not user preference.

    ``completion_required`` is intentionally derived from the
    override's ``session_flag`` (``bool(config.session_flag)``)
    rather than inherited from the built-in.  The built-in's value
    is structurally tied to its own ``session_flag``, so when the
    user overrides ``session_flag`` they also implicitly redefine
    whether the agent requires an explicit completion signal.
    Inheriting the built-in's boolean here would silently
    desynchronize the spec from the config the override carries.

    The synthesized support carries ``is_builtin=True`` so a subsequent
    override can replace it as well (see
    :meth:`AgentCatalog.replace_builtin`).
    """
    spec = AgentSpec.from_agent_config(
        config,
        interactive=builtin.spec.interactive,
        completion_required=bool(config.session_flag),
        no_default_session_flag=builtin.spec.no_default_session_flag,
    )
    return AgentSupport(
        name=name,
        spec=spec,
        parser_factory=builtin.parser_factory,
        strategy_factory=builtin.strategy_factory,
        config=config,
        is_builtin=True,
        no_default_session_flag=builtin.spec.no_default_session_flag,
    )


def _seed_catalog_with_builtins(catalog: AgentCatalog) -> None:
    for support in builtin_supports():
        if catalog.get(support.name) is None:
            register_agent_support_to_catalog(support.name, support, catalog)


class AgentRegistry:
    """Registry of available AI agents.

    The registry maintains a mapping of agent names to their configurations.
    It supports loading agents from UnifiedConfig and resolving agent
    names at runtime.

    Attributes:
        agents: Dictionary mapping agent names to their configurations.
    """

    def __init__(
        self,
        *,
        ccs_defaults: CcsConfig | None = None,
        catalog: AgentCatalog | None = None,
    ) -> None:
        """Initialize an empty agent registry."""
        self.agents: dict[str, AgentConfig] = {}  # bounded-accumulator-ok: bounded
        self._ccs_defaults = ccs_defaults or CcsConfig()
        if catalog is not None:
            self._catalog = catalog
            _seed_catalog_with_builtins(self._catalog)
        else:
            self._catalog = default_catalog()

    @property
    def catalog(self) -> AgentCatalog:
        """Return the ``AgentCatalog`` bound to this registry.

        When no catalog is injected at construction time, the registry falls
        back to :func:`ralph.agents.catalog.default_catalog`. ``register_agent_support``
        uses this property to write into the caller-owned catalog only, so a
        fresh ``AgentRegistry(catalog=AgentCatalog())`` does not leak
        registrations into the global default catalog.
        """
        return self._catalog

    @classmethod
    def from_config(cls, config: UnifiedConfig) -> AgentRegistry:
        """Create registry from UnifiedConfig.

        Args:
            config: Unified configuration containing agent definitions.

        Returns:
            Populated AgentRegistry instance.
        """
        registry = cls(ccs_defaults=config.ccs)
        _seed_catalog_with_builtins(default_catalog())

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
        support: object = getattr(config, "_support", None)
        if isinstance(support, AgentSupport):
            if (
                self._catalog is not None
                and self._catalog.get(support.name) is None
            ):
                self._catalog.add(support)
            return

        # The supplied config has no attached ``_support`` (e.g. it came
        # straight from ``UnifiedConfig.agents`` via ``from_config``).
        # If ``name`` matches a built-in agent, the user is overriding
        # a built-in: install the override on the public catalog
        # surface as well so ``registry.catalog.get(name)`` and the
        # ``<name>/<model>`` dynamic alias resolvers all see the
        # configured command, not the built-in.
        builtin = _find_builtin_support(name)
        if builtin is not None and self._catalog is not None:
            override_support = _synthesize_override_support(name, config, builtin)
            self._catalog.replace_builtin(name, override_support)
            object.__setattr__(config, "_support", override_support)

    def unregister(self, name: str) -> None:
        """Unregister an agent from the registry and the bound catalog.

        Args:
            name: Agent name.
        """
        self.agents.pop(name, None)
        if self._catalog is not None:
            self._catalog.remove(name)

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
        return _resolve_dynamic_agent(
            name,
            self._ccs_defaults,
            base_lookup=self.agents.get,
        )

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


def _resolve_dynamic_agent(  # noqa: PLR0911, PLR0912  # reason: dispatcher; per-prefix branches each return early on validation failure
    name: str,
    ccs_defaults: CcsConfig,
    *,
    base_lookup: Callable[[str], AgentConfig | None] | None = None,
) -> AgentConfig | None:
    """Resolve a documented dynamic alias to a synthesized :class:`AgentConfig`.

    Args:
        name: Dynamic alias (e.g. ``pi/<model>``, ``opencode/<model>``,
            ``nanocoder/<provider>/<model>``, ``agy/<model>``,
            ``claude-headless/<model>``, ``claude/<model>``, ``ccs/<alias>``).
        ccs_defaults: Default CCS configuration for ``ccs/<alias>`` resolution.
        base_lookup: Optional callable taking a base agent name (e.g.
            ``"pi"``) and returning the effective :class:`AgentConfig`
            for that name, accounting for any configured
            ``[agents.<name>]`` override.  When ``None`` (default), the
            resolver falls back to the built-in configurations.

    Returns:
        The synthesized :class:`AgentConfig` with the per-alias
        ``model_flag`` / ``cmd`` / ``session_flag`` overrides applied,
        or ``None`` if ``name`` does not match any documented alias
        pattern.
    """
    segments = name.split("/")
    resolved: AgentConfig | None = None

    def _base(agent_name: str) -> AgentConfig | None:
        """Resolve the effective base config for ``agent_name``.

        Prefers the configured override (via ``base_lookup``); falls
        back to the built-in.  Returns a fresh ``deepcopy`` so the
        resolver can safely call ``model_copy(update=...)`` without
        mutating the source.
        """
        if base_lookup is not None:
            override = base_lookup(agent_name)
            if override is not None:
                return deepcopy(override)
        builtin = builtin_agents().get(agent_name)
        return deepcopy(builtin) if builtin is not None else None

    if name.startswith("opencode/"):
        if len(segments) < _MIN_OPENCODE_SEGMENTS or not all(segments[1:]):
            return None

        base_config = _base("opencode")
        if base_config is None:
            return None
        dynamic_overrides: dict[str, object] = {
            "model_flag": f"-m {_normalize_opencode_model_id(name)}",
            "can_commit": True,
        }
        resolved = base_config.model_copy(update=dynamic_overrides)
    elif name.startswith("nanocoder/"):
        if len(segments) < _MIN_NANOCODER_PROVIDER_SEGMENTS or not all(segments[1:]):
            return None

        base_config = _base("nanocoder")
        if base_config is None:
            return None
        provider, model = _normalize_nanocoder_provider_and_model(name)
        model_flag = f"--provider {shlex.quote(provider)}"
        if model is not None:
            model_flag += f" --model {shlex.quote(model)}"
        nanocoder_overrides: dict[str, object] = {"model_flag": model_flag, "can_commit": True}
        resolved = base_config.model_copy(update=nanocoder_overrides)
    elif name.startswith("agy/"):
        if len(segments) < _MIN_AGY_SEGMENTS or not segments[1]:
            return None

        base_config = _base("agy")
        if base_config is None:
            return None
        # AGY model IDs from `agy models` are display names and may contain
        # spaces/parentheses (e.g. "Claude Sonnet 4.6 (Thinking)"). Quote the
        # value so shlex.split in the command builder keeps it as one argument.
        agy_overrides: dict[str, object] = {
            "model_flag": f"--model {shlex.quote(segments[1])}",
            "can_commit": True,
        }
        resolved = base_config.model_copy(update=agy_overrides)
    elif name.startswith("pi/"):
        model_id = name.removeprefix("pi/")
        if len(segments) < _MIN_PI_SEGMENTS or not _is_valid_pi_model_id(model_id):
            return None

        base_config = _base("pi")
        if base_config is None:
            return None
        # Pi's documented --model pattern is `provider/id` (e.g.
        # `anthropic/claude-sonnet-4-20250514`) with an optional
        # `:<thinking>` suffix (e.g. `:high`).  The full suffix after
        # `pi/` MUST be preserved verbatim, so we use
        # ``name.removeprefix('pi/')`` (NOT ``segments[1]``) which would
        # drop everything after the first `/` inside the model id.
        # https://pi.dev/docs/latest/usage: --model "Model pattern or ID;
        # supports provider/id and optional :<thinking>".
        pi_overrides: dict[str, object] = {
            "model_flag": f"--model {shlex.quote(model_id)}",
            "can_commit": True,
        }
        resolved = base_config.model_copy(update=pi_overrides)
    elif len(segments) == _CLAUDE_MODEL_SEGMENTS and segments[1]:
        if name.startswith("ccs/"):
            resolved = _resolve_dynamic_ccs_agent(name, ccs_defaults)
        elif name.startswith("claude-headless/"):
            base_config = _base("claude-headless")
            if base_config is None:
                return None
            claude_headless_overrides: dict[str, object] = {"model_flag": f"--model {segments[1]}"}
            resolved = base_config.model_copy(update=claude_headless_overrides)
        elif name.startswith("claude/"):
            base_config = _base("claude")
            if base_config is None:
                return None
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


def _is_valid_pi_model_id(model_id: str) -> bool:
    """Validate a ``pi/<model>`` model id per the documented pi.dev pattern.

    Per https://pi.dev/docs/latest/usage: ``--model <pattern>`` documents
    that the pattern may be a ``provider/id`` pair (e.g.
    ``anthropic/claude-sonnet-4-20250514``) or a plain model id, with an
    optional ``:<thinking>`` suffix (e.g. ``:high``).  The validator
    rejects any shape that would emit an undocumented
    ``--model <garbage>`` flag downstream:

      * empty model id (e.g. ``pi/``, ``pi//``)
      * whitespace, newline, or carriage return anywhere in the id
        (pi's --model pattern is a single argv token; the
        ``PiCommandBuilder`` tokenization in
        ``ralph/agents/invoke/_command_builders/__init__.py``
        relies on this invariant to emit a clean ``--model <value>``
        argv pair instead of a shlex-rejoined garbage token like
        ``['--model', "'foo", "bar'"]``)
      * more than one ``:`` separator (only the optional
        ``:<thinking>`` suffix is allowed; multi-colon shapes like
        ``pi/foo:bar:baz`` fall outside the documented
        ``provider/id[:<thinking>]`` syntax)
      * more than one ``/`` separator (the documented pattern is at
        most one ``/`` between provider and id; multi-slash shapes
        like ``pi/provider/model/extra`` or ``pi/anthropic/claude/foo``
        are not documented at https://pi.dev/docs/latest/usage and
        must NOT be silently accepted - they would round-trip as
        ``--model provider/model/extra`` which is an undocumented
        pattern and may be silently misparsed downstream)
      * empty provider segment when ``/`` is present (e.g. ``pi//x``)
      * empty model-name segment when ``/`` is present (e.g.
        ``pi/provider/``)
      * empty base before the optional ``:<thinking>`` colon (e.g.
        ``pi/:high``)
      * empty ``:<thinking>`` suffix (e.g. ``pi/anthropic/claude:``)

    A bare single-segment name with no ``/`` is accepted as a plain
    model id (e.g. ``pi/sonnet``, ``pi/claude-sonnet-4-20250514``).
    """
    if not model_id or any(ch.isspace() for ch in model_id):
        return False

    base, _, thinking = model_id.partition(":")
    has_thinking = bool(thinking)
    base_has_colon_split = ":" in model_id
    if base_has_colon_split and (not base or not thinking):
        return False
    if has_thinking and ":" in thinking:
        return False
    if "/" not in base:
        return True
    provider, _, model_name = base.partition("/")
    if "/" in model_name:
        return False
    return bool(provider and model_name)
