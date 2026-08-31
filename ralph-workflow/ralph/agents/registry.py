"""Agent registry: the source of truth for which agents Ralph Workflow can invoke.

The ``AgentRegistry`` is the in-memory index that maps every agent name Ralph
Workflow can route to (e.g. ``claude``, ``codex``, ``opencode``, ``agy``,
``nanocoder``, ``pi``, plus dynamic ``<agent>/<model>`` aliases) to the
``AgentConfig`` that describes how to invoke that agent.

Public surface at a glance:

- ``AgentRegistry`` — the registry itself; constructed either empty or
  pre-seeded with the bundled defaults via :meth:`AgentRegistry.from_config`
- ``AgentRegistry.from_config`` — build a registry from a
  :class:`ralph.config.models.UnifiedConfig`, layering user-global,
  project-local, and CLI overrides in the correct precedence order
- ``builtin_agents`` — the built-in default agent configurations that ship
  with Ralph Workflow; the registry seeds itself from this map when no
  explicit catalog override is provided
- ``AgentSpec`` — the internal declarative record that backs every
  ``AgentConfig`` in the registry (see ``ralph.agents.spec``)

When to use this module:

- You are extending Ralph Workflow with a new agent CLI. Use
  :func:`ralph.agents.registration.register_agent_support_to_catalog` to
  register the new agent support into the catalog, then construct an
  ``AgentRegistry`` with the catalog injected. The registry does not
  auto-seed at module import; you opt in by calling ``AgentRegistry(...)``
  or ``AgentRegistry.from_config(...)``.
- You are debugging a routing failure. The registry is what
  :mod:`ralph.pipeline.orchestrator` consults to resolve a phase's declared
  agent name to a command. If a phase fails with "unknown agent", the
  registry is where the missing name should be.
- You are writing a custom CLI command that needs to know which agents are
  available. Use ``AgentRegistry.from_config(unified_config)`` and inspect
  the resulting registry rather than reading config files directly.

Side effects:

- Construction does not spawn subprocesses, hit the network, or write
  files. The registry is a pure in-memory structure.
- Resolving an agent name does not require the underlying CLI binary to
  be installed; :func:`ralph.agents.availability.check_agent_availability`
  is what actually probes ``PATH``.
- The registry does not own credential handling. Authentication lives in
  the agent CLI itself (see the agent lifecycle page in the docs).

Invariants:

- The registry's keys are the agent names policy references (e.g.
  ``claude-headless``, ``agy/gemini-3.6-flash-low``). The registry
  does not silently rename or normalize these strings.
- The registry does not silently drop unknown agent names; resolution
  raises :class:`ralph.agents.unknown_agent_error.UnknownAgentError`.
- Built-in agents are seeded by ``from_config``; an explicitly constructed
  ``AgentRegistry(catalog=...)`` seeds from the injected catalog via
  ``_seed_catalog_with_builtins``. A bare ``AgentRegistry()`` does not
  seed; pass a catalog or call ``from_config``.

Testing notes:

- ``ralph.testing.fake_agent_executor.FakeAgentExecutor`` swaps the
  process-execution layer for tests; the registry itself remains a pure
  index and does not need fakes.
- The seeded default catalog is reachable as
  ``ralph.agents.catalog.default_catalog``.
"""

from __future__ import annotations

import importlib
import shlex
from copy import deepcopy
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.agents.catalog import AgentCatalog, default_catalog
from ralph.agents.idle_watchdog import SubagentPidRegistry
from ralph.agents.registration import register_agent_support_to_catalog
from ralph.agents.spec import AgentSpec
from ralph.agents.support import (
    _DYNAMIC_ALIAS_HELP_BY_PREFIX,
    _EMPTY_OUTPUT_DIAGNOSTIC_FACTORY_BY_PREFIX,
    AgentSupport,
)
from ralph.agents.vision_agent_provisioning import provision_vision_verdict_agent
from ralph.config.ccs_config import CcsAliasConfig, CcsConfig
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig
from ralph.executor.process import ProcessExecutionError, ProcessRunOptions, run_process
from ralph.process.monitor import (
    make_agy_subagent_pid_source,
    make_claude_interactive_subagent_pid_source,
    make_claude_subagent_pid_source,
    make_codex_subagent_pid_source,
    make_cursor_subagent_pid_source,
    make_generic_subagent_pid_source,
    make_kimi_subagent_pid_source,
    make_nanocoder_subagent_pid_source,
    make_opencode_subagent_pid_source,
    make_pi_subagent_pid_source,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.language_detector.models import ProjectStack
    from ralph.process.monitor import SubagentPidSource
    from ralph.workspace import Workspace

_MIN_OPENCODE_SEGMENTS = 2
_MIN_NANOCODER_PROVIDER_SEGMENTS = 2
_MIN_AGY_SEGMENTS = 2
_MIN_PI_SEGMENTS = 2
_CLAUDE_MODEL_SEGMENTS = 2
_CODEX_REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh"})
_AGY_REASONING_EFFORTS = frozenset({"low", "medium", "high"})
# Measured from `agy models` v1.1.8; refresh only from a new AGY measurement.
_AGY_MODELS = frozenset(
    {
        "gemini-3.6-flash-high",
        "gemini-3.6-flash-medium",
        "gemini-3.6-flash-low",
        "gemini-3.5-flash-high",
        "gemini-3.5-flash-medium",
        "gemini-3.5-flash-low",
        "gemini-3.1-pro-high",
        "gemini-3.1-pro-low",
        "claude-sonnet-4-6",
        "claude-opus-4-6-thinking",
        "gpt-oss-120b-medium",
    }
)

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig


def _make_default_agy_models_probe() -> Callable[[], str]:
    """Build the bounded, per-process cached probe for AGY's published models."""
    cached_output: str | None = None

    def probe() -> str:
        nonlocal cached_output
        if cached_output is None:
            try:
                result = run_process(
                    "agy",
                    ("models",),
                    options=ProcessRunOptions(
                        capture_output=True, timeout=5.0, label="agents:agy-models"
                    ),
                )
            except (OSError, ProcessExecutionError):
                cached_output = ""
            else:
                cached_output = result.stdout if result.returncode == 0 else ""
        return cached_output

    return probe


_default_agy_models_probe = _make_default_agy_models_probe()


def agy_published_models() -> tuple[str, ...]:
    """Return currently published AGY IDs, falling back to the measured v1.1.8 pin."""
    try:
        observed = tuple(
            # AGY v1.1.8+ emits ``ID\tDescription`` lines; keep only the ID
            # column so ``_parse_agy_alias`` matches bare IDs. A line with no
            # tab splits to a single element (the whole line), so this is
            # forward-compatible with the older bare-ID output format.
            line.strip().lstrip("- ").split("\t", 1)[0]
            for line in _default_agy_models_probe().splitlines()
            if line.strip() and not line.endswith(":")
        )
    except (OSError, ProcessExecutionError):
        observed = ()
    return tuple(sorted(set(observed) or _AGY_MODELS))


def agy_reasoning_efforts() -> tuple[str, ...]:
    """Return the effort vocabulary published by AGY's v1.1.8 help output."""
    return ("low", "medium", "high")


def agy_alias_help() -> str:
    """Return the actionable AGY alias vocabulary for operator-facing failures."""
    return (
        f"Available AGY models: {', '.join(agy_published_models())}. "
        "Effort suffixes are not supported when a model is given explicitly; "
        "use the bare published model ID (e.g. agy/gemini-3.6-flash-low)."
    )


def builtin_supports() -> tuple[AgentSupport, ...]:
    """Return the built-in :class:`AgentSupport` rows.

    Thin module-level wrapper over :func:`ralph.agents.builtin.builtin_supports`;
    deferred import keeps the registry<->builtin dependency graph acyclic (the
    AGY declarative entry references :func:`agy_alias_help` from this module).
    """
    builtin = importlib.import_module("ralph.agents.builtin")
    _builtin_supports_impl = cast(
        "Callable[[], tuple[AgentSupport, ...]]",
        builtin.builtin_supports,
    )
    return _builtin_supports_impl()


def _builtin_supports_lazy() -> tuple[AgentSupport, ...]:
    """Backward-compatible alias for :func:`builtin_supports`."""
    return builtin_supports()


def _lookup_prefix_factory[F](
    agent_name: str,
    table: dict[str, F],
) -> F | None:
    """Return the factory registered under ``agent_name``'s longest ``/`` prefix.

    Walks ``agent_name``'s ``/``-separated segments from the full name down
    to the first segment (e.g. ``a/b/c`` → ``a/b/c``, ``a/b``, ``a``) and
    returns the first table hit, or ``None`` when no prefix is registered.
    """
    segments = agent_name.split("/")
    for end in range(len(segments), 0, -1):
        factory = table.get("/".join(segments[:end]))
        if factory is not None:
            return factory
    return None


def lookup_dynamic_alias_help(
    agent_name: str,
    catalog: AgentCatalog | None = None,
) -> str | None:
    """Return the registered alias help string for ``agent_name``, or ``None``.

    Two-phase lookup over registration data (no agent-name substring
    checks):

    - Phase 1 (catalog exact match): when ``catalog.get(agent_name)``
      resolves to a support carrying ``dynamic_alias_help``, invoke it and
      return its string. This is the registered-name path.
    - Phase 2 (registered-prefix fallback): when the name never resolves
      in the catalog, walk its longest ``/`` prefix through the
      module-level ``_DYNAMIC_ALIAS_HELP_BY_PREFIX`` table populated by
      :meth:`AgentSupport.from_registration_kwargs`, so an *unknown*
      alias under a registered prefix (e.g. ``agy/<unknown-model>``)
      still surfaces the agent's help string.

    Args:
        agent_name: Agent name or dynamic alias to look up.
        catalog: Catalog to consult for Phase 1; defaults to the default
            catalog.

    Returns:
        The help string, or ``None`` when no phase hits.
    """
    resolved = default_catalog() if catalog is None else catalog
    support = resolved.get(agent_name)
    if support is not None and support.dynamic_alias_help is not None:
        return support.dynamic_alias_help()
    factory = _lookup_prefix_factory(agent_name, _DYNAMIC_ALIAS_HELP_BY_PREFIX)
    if factory is not None:
        return factory()
    return None


def lookup_empty_output_diagnostic_factory(
    agent_name: str,
    catalog: AgentCatalog | None = None,
) -> Callable[[list[str], Path | None], str | None] | None:
    """Return the registered empty-output diagnostic factory for ``agent_name``.

    Same two-phase semantics as :func:`lookup_dynamic_alias_help`:
    Phase 1 resolves ``agent_name`` in the catalog and returns the
    support's ``empty_output_diagnostic_factory`` when set; Phase 2 falls
    back to the longest ``/``-prefix match against the module-level
    ``_EMPTY_OUTPUT_DIAGNOSTIC_FACTORY_BY_PREFIX`` table populated by
    :meth:`AgentSupport.from_registration_kwargs`, so an unknown alias
    under a registered prefix (e.g. ``agy/<unknown-model>``) still gets
    the agent's diagnostic.

    Args:
        agent_name: Agent name or dynamic alias to look up.
        catalog: Catalog to consult for Phase 1; defaults to the default
            catalog.

    Returns:
        The diagnostic factory, or ``None`` when no phase hits.
    """
    resolved = default_catalog() if catalog is None else catalog
    support = resolved.get(agent_name)
    if support is not None and support.empty_output_diagnostic_factory is not None:
        return support.empty_output_diagnostic_factory
    return _lookup_prefix_factory(agent_name, _EMPTY_OUTPUT_DIAGNOSTIC_FACTORY_BY_PREFIX)


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
    for support in _builtin_supports_lazy():
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
        display_capabilities=builtin.display_capabilities,
        dynamic_alias_help=builtin.dynamic_alias_help,
        dynamic_alias_help_prefix=builtin.dynamic_alias_help_prefix,
        empty_output_diagnostic_factory=builtin.empty_output_diagnostic_factory,
        empty_output_diagnostic_prefix=builtin.empty_output_diagnostic_prefix,
    )


def _seed_catalog_with_builtins(catalog: AgentCatalog) -> None:
    for support in _builtin_supports_lazy():
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
            self._catalog: AgentCatalog = catalog
            _seed_catalog_with_builtins(self._catalog)
        else:
            self._catalog = default_catalog()
        # Explicit annotation: the constructor narrows _catalog to AgentCatalog.
        self._catalog_typed: AgentCatalog = self._catalog

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
    def from_config(
        cls,
        config: UnifiedConfig,
        *,
        catalog: AgentCatalog | None = None,
    ) -> AgentRegistry:
        """Create registry from UnifiedConfig.

        Args:
            config: Unified configuration containing agent definitions.

        Returns:
            Populated AgentRegistry instance.
        """
        registry = cls(ccs_defaults=config.ccs, catalog=catalog)
        if catalog is None:
            _seed_catalog_with_builtins(default_catalog())

        for name, agent_config in builtin_agents().items():
            registry.register(name, agent_config)

        for name, agent_config in config.agents.items():
            registry.register(name, agent_config)

        for alias, alias_value in config.ccs_aliases.items():
            registry.register(f"ccs/{alias}", _resolve_ccs_alias(alias_value, config.ccs))

        logger.debug("Loaded {} agents from config", len(registry.agents))
        return registry

    def build_subagent_pid_registry(
        self,
        transport: AgentTransport | str,
    ) -> tuple[SubagentPidRegistry, SubagentPidSource]:
        """Construct a per-invocation ``SubagentPidRegistry`` + ``SubagentPidSource``.

        R1 (Trustworthy Idle Watchdog spec): a single shared
        ``SubagentPidRegistry`` is created per invocation and threaded
        into both the execution strategy (via
        ``subagent_pid_source=``) and the parser (via
        ``subagent_pid_registry=``) so any PID registered by either
        layer becomes visible to ``ProcessMonitor.spawned_subagent_count()``.

        The per-transport factory helpers in
        ``ralph.process.monitor._subagent_pid_source_providers`` wrap
        the shared registry to expose a ``SubagentPidSource`` that
        filters by transport source label. OpenCode's
        ``ChildLivenessSubagentPidSource`` continues to use its own
        ``ChildLivenessRegistry`` (the registry is shared but the
        source adapter is transport-specific).

        Returns:
            A ``(registry, source)`` tuple. The registry is the single
            source of truth (FIFO-bounded at 1024 entries); the source
            is the per-transport adapter the watchdog consumes.
        """
        registry = SubagentPidRegistry()
        if isinstance(transport, AgentTransport):
            transport_name: str = transport.value
        else:
            transport_name = transport
        factory_map: dict[str, Callable[[SubagentPidRegistry], SubagentPidSource]] = {
            "opencode": make_opencode_subagent_pid_source,
            "claude": make_claude_subagent_pid_source,
            "pi": make_pi_subagent_pid_source,
            "agy": make_agy_subagent_pid_source,
            "claude_interactive": make_claude_interactive_subagent_pid_source,
            "codex": make_codex_subagent_pid_source,
            "generic": make_generic_subagent_pid_source,
            # Nanocoder shares the generic wire format (no per-transport
            # structured child events) but the watchdog's per-transport
            # ``SubagentPidSource`` filter (R1) is keyed on the
            # ``AgentTransport`` enum, so it gets its own canonical
            # factory that binds the ``"nanocoder"`` source label.
            "nanocoder": make_nanocoder_subagent_pid_source,
            "cursor": make_cursor_subagent_pid_source,
            "kimi": make_kimi_subagent_pid_source,
        }
        factory = factory_map.get(transport_name)
        if factory is None:
            msg = (
                f"no SubagentPidSource factory for transport {transport!r}; expected one of"
                f" {sorted(factory_map)}"
            )
            raise ValueError(msg)
        return registry, factory(registry)

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
            if self._catalog is not None and self._catalog.get(support.name) is None:
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
            if self._catalog.get(name) is None:
                self._catalog.add(override_support)
            else:
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

    def provision_vision_verdict_agent(
        self,
        *,
        workspace: Workspace | None = None,
        stack: ProjectStack | None = None,
    ) -> bool:
        """Register the vision-verdict agent when the design-system policy applies.

        Thin wrapper around
        :func:`ralph.agents.vision_agent_provisioning.provision_vision_verdict_agent`
        that owns the catalog write through ``self._catalog`` and the
        ``self.agents`` map so the registry stays the single
        source of truth for agent availability.

        The agent is conditional: it is registered only when the
        design-system policy is in scope for the active workspace.
        A workspace without a design-system policy is
        fail-closed against criteria 13/15; provisioning a
        non-functional agent would mask that failure mode, so
        the registry intentionally does NOT seed the agent
        eagerly from :meth:`from_config` — the caller MUST
        invoke this method after the design-system policy
        detector has run.

        Args:
            workspace: The active workspace protocol object.
                Optional for testability; the production caller
                passes the real :class:`Workspace` so the
                deterministic signal set is available.
            stack: The detected project stack. Optional for
                testability; the production caller passes the
                real :class:`ProjectStack` so the framework
                and CSS family signals are available.

        Returns:
            ``True`` when the agent was registered (or was
            already wired in), ``False`` when the design-system
            policy is not in scope.
        """
        registered = provision_vision_verdict_agent(
            self._catalog,
            workspace=workspace if workspace is not None else None,
            stack=stack if stack is not None else None,
        )
        if not registered:
            return False
        # Keep ``self.agents`` in lockstep with the catalog so
        # ``registry.get("vision-verdict")`` and
        # ``registry.list_agents()`` see the same surface the
        # catalog exposes.
        support = self._catalog.get("vision-verdict")
        if support is not None:
            self.agents["vision-verdict"] = support.config
        return True

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


def _resolve_dynamic_agent(
    name: str,
    ccs_defaults: CcsConfig,
    *,
    base_lookup: Callable[[str], AgentConfig | None] | None = None,
) -> AgentConfig | None:
    """Resolve a documented dynamic alias to a synthesized :class:`AgentConfig`.

    Args:
        name: Dynamic alias (e.g. ``pi/<model>``, ``opencode/<model>``,
            ``nanocoder/<provider>/<model>``, ``agy/<model>``,
            ``claude-headless/<model>``, ``claude/<model>``,
            ``kimi/<model>``, ``ccs/<alias>``).
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

    if name.startswith("codex/"):
        resolved = _resolve_dynamic_codex_agent(name, _base("codex"))
    elif name.startswith("pi/"):
        model_id = name.removeprefix("pi/")
        if len(segments) < _MIN_PI_SEGMENTS or not _is_valid_pi_model_id(model_id):
            return None

        base_config = _base("pi")
        if base_config is None:
            return None
        # Pi's --model pattern accepts provider/model identifiers with
        # an optional `:<thinking>` suffix (e.g. `:high`).  The full suffix after
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
    elif name.startswith("cursor/"):
        # Cursor's documented model ids may include bracket parameterization
        # (``claude-opus-4-8[context=1m,effort=high,fast=false]``), nested
        # slashes, and thinking-variant suffixes.  The full suffix after
        # ``cursor/`` MUST be preserved verbatim, so we use
        # ``name.removeprefix('cursor/')`` (NOT ``segments[1]``) which
        # would drop everything after the first ``/`` inside the model id.
        # ``cursor/auto`` is the explicit Auto alias; ``cursor`` alone is
        # resolved to the built-in's default --yolo + Auto routing.
        if not _is_valid_cursor_model_id(name.removeprefix("cursor/")):
            return None
        model_id = name.removeprefix("cursor/")
        if model_id == "":
            return None

        base_config = _base("cursor")
        if base_config is None:
            return None
        # ``--model <value>`` is a single argv pair.  ``shlex.quote``
        # keeps the bracket-parameterized id in one argv token, and the
        # template.format() + split() path in
        # :class:`CursorCommandBuilder._build_model_flag` tokenizes
        # the resulting ``--model 'claude-opus-4-8[...]'`` as exactly
        # two argv tokens (--model, <value>).
        cursor_overrides: dict[str, object] = {
            "model_flag": f"--model {shlex.quote(model_id)}",
            "can_commit": True,
        }
        resolved = base_config.model_copy(update=cursor_overrides)
    elif name.startswith("kimi/"):
        resolved = _resolve_dynamic_kimi_agent(name, _base)
    elif name.startswith(("opencode/", "nanocoder/", "agy/")):
        resolved = _resolve_dynamic_simple_prefixed_agent(name, segments, _base)
    elif len(segments) == _CLAUDE_MODEL_SEGMENTS and segments[1]:
        resolved = _resolve_dynamic_claude_family(name, ccs_defaults, _base)

    return resolved


def _resolve_dynamic_kimi_agent(
    name: str,
    base_lookup: Callable[[str], AgentConfig | None],
) -> AgentConfig | None:
    """Resolve a ``kimi/<model>`` dynamic alias to a synthesized config."""
    # Kimi's documented model ids are slash-delimited alias paths
    # (e.g. ``kimi-code/k3-256k``).  The full suffix after ``kimi/``
    # MUST be preserved verbatim, so we use
    # ``name.removeprefix('kimi/')`` (NOT ``segments[1]``) which
    # would drop everything after the first ``/`` inside the model
    # id.  ``kimi`` alone resolves to the built-in entry.
    model_id = name.removeprefix("kimi/")
    if not _is_valid_kimi_model_id(model_id):
        return None

    base_config = base_lookup("kimi")
    if base_config is None:
        return None
    # ``-m <value>`` is kimi's documented short model flag
    # (``-m, --model <model>``), emitted as a single argv pair;
    # ``shlex.quote`` keeps the slash-delimited alias path in one
    # argv token through the KimiCommandBuilder template split.
    kimi_overrides: dict[str, object] = {
        "model_flag": f"-m {shlex.quote(model_id)}",
        "can_commit": True,
    }
    return base_config.model_copy(update=kimi_overrides)


def _resolve_dynamic_simple_prefixed_agent(
    name: str,
    segments: list[str],
    base_lookup: Callable[[str], AgentConfig | None],
) -> AgentConfig | None:
    """Resolve dynamic aliases whose model suffixes need only basic validation."""
    if name.startswith("opencode/"):
        if len(segments) >= _MIN_OPENCODE_SEGMENTS and all(segments[1:]):
            base_config = base_lookup("opencode")
            if base_config is not None:
                # ``-m <value>`` is opencode's documented short model flag
                # (``-m, --model <model>``, ``opencode run --help`` on
                # 1.18.25) and is emitted as a single argv pair.
                # ``shlex.quote`` keeps the whole model id in ONE argv
                # token, matching the kimi / claude / codex / cursor
                # dynamic-alias quoting contract; without it an alias
                # carrying whitespace (``opencode/minimax/M3 --agent plan``)
                # split into extra argv tokens and smuggled flags onto
                # the opencode command line.
                model_id = _opencode_alias_model_id(name)
                return base_config.model_copy(
                    update={
                        "model_flag": f"-m {shlex.quote(model_id)}",
                        "can_commit": True,
                    }
                )
    elif name.startswith("nanocoder/"):
        if len(segments) >= _MIN_NANOCODER_PROVIDER_SEGMENTS and all(segments[1:]):
            base_config = base_lookup("nanocoder")
            if base_config is not None:
                provider, model = _normalize_nanocoder_provider_and_model(name)
                model_flag = f"--provider {shlex.quote(provider)}"
                if model is not None:
                    model_flag += f" --model {shlex.quote(model)}"
                return base_config.model_copy(update={"model_flag": model_flag, "can_commit": True})
    elif name.startswith("agy/") and len(segments) >= _MIN_AGY_SEGMENTS:
        alias = _parse_agy_alias(
            name.removeprefix("agy/"), models=frozenset(agy_published_models())
        )
        base_config = base_lookup("agy")
        if alias is not None and base_config is not None:
            model_id, effort = alias
            model_flag = f"--model {shlex.quote(model_id)}"
            if effort is not None:
                model_flag += f" --effort {effort}"
            return base_config.model_copy(
                update={"model": model_id, "model_flag": model_flag, "can_commit": True}
            )
    return None


def _parse_agy_alias(
    alias_value: str, *, models: frozenset[str] = _AGY_MODELS
) -> tuple[str, str | None] | None:
    """Parse an observed AGY model alias without silently changing its selection.

    AGY v1.1.8 accepts the published model IDs. The latest measured ledger
    conclusion is that ``--effort`` is accepted only without an explicit
    model, so Ralph aliases always take the form ``agy/<published-model-id>``.
    Any ``:<effort>`` suffix is rejected before invocation rather than
    emitting an unsupported ``--model <id> --effort <tier>`` selection.
    """
    model_id, separator, _effort = alias_value.partition(":")
    if model_id not in models:
        return None
    if not separator:
        return model_id, None
    return None


def _resolve_dynamic_ccs_agent(name: str, ccs_defaults: CcsConfig) -> AgentConfig | None:
    segments = name.split("/")
    if len(segments) != _CLAUDE_MODEL_SEGMENTS or not segments[1]:
        return None
    return _resolve_ccs_alias(f"ccs {segments[1]}", ccs_defaults)


def _resolve_dynamic_claude_family(
    name: str,
    ccs_defaults: CcsConfig,
    base_lookup: Callable[[str], AgentConfig | None],
) -> AgentConfig | None:
    """Resolve the compact Claude and CCS dynamic-alias family."""
    segments = name.split("/")
    if name.startswith("ccs/"):
        return _resolve_dynamic_ccs_agent(name, ccs_defaults)
    if name.startswith("claude-headless/"):
        base_config = base_lookup("claude-headless")
    elif name.startswith("claude/"):
        base_config = base_lookup("claude")
    else:
        return None
    if base_config is None:
        return None
    # ``--model <value>`` is a single argv pair; ``shlex.quote`` keeps a
    # model id containing shell-special characters (whitespace, brackets,
    # quotes) in one argv token, matching the cursor / codex / pi
    # dynamic-alias quoting contract. Both Claude command builders
    # tokenize the flag with ``shlex.split``, so quoting here is what
    # preserves the single-token guarantee end to end.
    return base_config.model_copy(
        update={
            "model_flag": f"--model {shlex.quote(segments[1])}",
            "model": segments[1],
        }
    )


def _opencode_alias_model_id(name: str) -> str:
    """Strip the Ralph ``opencode/`` ALIAS prefix -- exactly once, here.

    opencode publishes a provider literally named ``opencode``
    (``opencode models`` lists ``opencode/big-pickle``,
    ``opencode/nemotron-3-ultra-free``, ...) and parses ``-m`` as
    ``provider/model``. The alias ``opencode/opencode/big-pickle``
    therefore has to yield ``opencode/big-pickle``. This function is the
    single, canonical strip point: nothing downstream may remove the
    prefix again.
    """
    return name.removeprefix("opencode/")


def _resolve_dynamic_codex_agent(name: str, base_config: AgentConfig | None) -> AgentConfig | None:
    """Resolve a validated Codex model alias against its effective base config."""
    codex_alias = _parse_codex_alias(name.removeprefix("codex/"))
    if codex_alias is None or base_config is None:
        return None
    model_id, effort = codex_alias
    model_flag = f"--model {shlex.quote(model_id)}"
    if effort is not None:
        effort_override = f'model_reasoning_effort = "{effort}"'
        model_flag += f" -c {shlex.quote(effort_override)}"
    return base_config.model_copy(
        update={"model": model_id, "model_flag": model_flag, "can_commit": True}
    )


def _parse_codex_alias(alias_value: str) -> tuple[str, str | None] | None:
    """Parse a safe ``codex/<model>[effort=<level>]`` dynamic alias."""
    model_id, separator, suffix = alias_value.partition("[")
    if (
        not model_id
        or any(char.isspace() for char in model_id)
        or not all(segment for segment in model_id.split("/"))
    ):
        return None
    if not separator:
        return model_id, None
    effort_prefix = "effort="
    effort = suffix.removesuffix("]")
    if (
        not suffix.endswith("]")
        or suffix.count("[")
        or suffix.count("]") != 1
        or not effort.startswith(effort_prefix)
        or effort.removeprefix(effort_prefix) not in _CODEX_REASONING_EFFORTS
    ):
        return None
    return model_id, effort.removeprefix(effort_prefix)


def _normalize_nanocoder_provider_and_model(name: str) -> tuple[str, str | None]:
    parts = name.removeprefix("nanocoder/").split("/")
    provider = parts[0]
    model = "/".join(parts[1:]) if len(parts) > 1 else None
    return provider, model


def _is_valid_pi_model_id(model_id: str) -> bool:
    """Validate a ``pi/<model>`` model id for argv-safe provider/model parity.

    ``--model <pattern>`` is emitted as a single argv value, so the
    resolver accepts the same slash-delimited provider/model path shape
    supported by the other model-addressable agents while rejecting
    shapes that would create empty or ambiguous argv values:

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
      * empty provider/model path segments when ``/`` is present (e.g.
        ``pi//x``, ``pi/provider/``, ``pi/provider//model``)
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
    return all(segment for segment in base.split("/"))


def _is_valid_cursor_model_id(model_id: str) -> bool:
    """Validate a ``cursor/<model>`` model id for argv-safe preservation.

    Cursor's documented model catalog spans multiple upstream providers
    (OpenAI Codex variants, Claude variants, Composer, Auto, etc.).
    The full id after ``cursor/`` MUST be preserved verbatim in the
    ``--model <value>`` argv pair, including:

      * bracket parameterization, e.g.
        ``cursor/claude-opus-4-8[context=1m,effort=high,fast=false]``
      * nested slash paths, e.g.
        ``cursor/anthropic/claude-sonnet-4-20250514``
      * thinking-variant suffixes, e.g.
        ``cursor/sonnet-4-thinking``,
        ``cursor/gpt-5.3-codex-xhigh``

    The resolver rejects shapes that would create empty or ambiguous
    argv values (and would silently route a wrong model):

      * empty model id (e.g. ``cursor/``, ``cursor//``)
      * whitespace, newline, or carriage return anywhere in the id
        (the ``CursorCommandBuilder`` tokenization in
        :mod:`ralph.agents.invoke._command_builders` relies on this
        invariant to emit a clean ``--model <value>`` argv pair
        instead of a shlex-rejoined garbage token like
        ``['--model', "'foo", "bar'"]``)
      * empty provider/model path segments when ``/`` is present
        (e.g. ``cursor//x``, ``cursor/provider/``,
        ``cursor/provider//model``)

    A bare single-segment name with no ``/`` is accepted as a plain
    model id (e.g. ``cursor/auto``, ``cursor/gpt-5.3-codex-high``).
    ``cursor/auto`` is the explicit Auto alias; ``cursor`` alone
    defaults to the built-in's Auto routing.
    """
    if not model_id or any(ch.isspace() for ch in model_id):
        return False
    return all(segment for segment in model_id.split("/"))


def _is_valid_kimi_model_id(model_id: str) -> bool:
    """Validate a ``kimi/<model>`` model id for argv-safe preservation.

    Kimi Code's documented model listing addresses models with plain
    ids and slash-delimited alias paths (e.g. ``kimi-for-coding``,
    ``kimi-code/k3-256k``).  The full id after ``kimi/`` MUST be
    preserved verbatim in the ``-m <value>`` argv pair, so the
    resolver rejects shapes that would create empty or ambiguous
    argv values (and would silently route a wrong model):

      * empty model id (e.g. ``kimi/``, ``kimi//``)
      * whitespace, newline, or carriage return anywhere in the id
        (the ``KimiCommandBuilder`` template split relies on this
        invariant to emit a clean ``-m <value>`` argv pair instead of
        a shlex-rejoined garbage token like ``['-m', "'foo", "bar'"]``)
      * a leading dash (e.g. ``-flag``), which the CLI would parse as
        an option instead of the ``-m`` value
      * any ``:`` separator (kimi's documented model ids carry no
        colon syntax; multi-colon shapes like ``foo:bar:baz`` fall
        outside the documented ``-m`` value grammar)
      * empty alias-path segments when ``/`` is present (e.g.
        ``kimi//x``, ``kimi/kimi-code/``, ``kimi/kimi-code//k3``)

    A bare single-segment name with no ``/`` is accepted as a plain
    model id (e.g. ``kimi/kimi-for-coding``).
    """
    if not model_id or any(ch.isspace() for ch in model_id):
        return False
    if model_id.startswith("-"):
        return False
    if ":" in model_id:
        return False
    return all(segment for segment in model_id.split("/"))
