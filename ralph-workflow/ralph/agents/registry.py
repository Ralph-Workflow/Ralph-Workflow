from __future__ import annotations

import shlex
from copy import deepcopy
from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents import builtin as builtin_module
from ralph.agents._codex_alias import resolve_codex_alias
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
_AGY_REASONING_EFFORTS = frozenset({"low", "medium", "high"})
_AGY_MODELS = frozenset(
    {
        "gemini-3.8-flash-high",
        "gemini-3.8-flash-medium",
        "gemini-3.8-flash-low",
        "gemini-3.7-flash-high",
        "gemini-3.7-flash-medium",
        "gemini-3.7-flash-low",
        "gemini-3.6-flash-high",
        "gemini-3.6-flash-medium",
        "gemini-3.6-flash-low",
        "gemini-3.1-pro-high",
        "gemini-3.1-pro-low",
        "claude-opus-5-5-low",
        "claude-opus-5-5-medium",
        "claude-opus-5-5-high",
        "claude-sonnet-5-5-low",
        "claude-sonnet-5-5-medium",
        "claude-sonnet-5-5-high",
        "gpt-oss-120b-medium",
    }
)


if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig


def _make_default_agy_models_probe() -> Callable[[], str]:
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
    try:
        observed = tuple(
            line.strip().lstrip("- ").split("\t", 1)[0]
            for line in _default_agy_models_probe().splitlines()
            if line.strip() and not line.endswith(":")
        )
    except (OSError, ProcessExecutionError):
        observed = ()
    return tuple(sorted(set(observed) or _AGY_MODELS))


def agy_reasoning_efforts() -> tuple[str, ...]:
    return ("low", "medium", "high")


def agy_alias_help() -> str:
    return (
        f"Available AGY models: {', '.join(agy_published_models())}. "
        "Effort suffixes are not supported when a model is given explicitly; "
        "use the bare published model ID (e.g. agy/gemini-3.8-flash-low)."
    )


def builtin_supports() -> tuple[AgentSupport, ...]:
    return builtin_module.builtin_supports()


def _builtin_supports_lazy() -> tuple[AgentSupport, ...]:
    return builtin_supports()


def _lookup_prefix_factory[F](
    agent_name: str,
    table: dict[str, F],
) -> F | None:
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
    resolved = default_catalog() if catalog is None else catalog
    support = resolved.get(agent_name)
    if support is not None and support.empty_output_diagnostic_factory is not None:
        return support.empty_output_diagnostic_factory
    return _lookup_prefix_factory(agent_name, _EMPTY_OUTPUT_DIAGNOSTIC_FACTORY_BY_PREFIX)


def builtin_agents() -> dict[str, AgentConfig]:
    return {support.name: support.config for support in builtin_supports()}


def _find_builtin_support(name: str) -> AgentSupport | None:
    for support in _builtin_supports_lazy():
        if support.name == name:
            return support
    return None


def _synthesize_override_support(
    name: str,
    config: AgentConfig,
    builtin: AgentSupport,
) -> AgentSupport:
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
    def __init__(
        self,
        *,
        ccs_defaults: CcsConfig | None = None,
        catalog: AgentCatalog | None = None,
    ) -> None:
        self.agents: dict[str, AgentConfig] = {}
        self._ccs_defaults = ccs_defaults or CcsConfig()
        if catalog is not None:
            self._catalog: AgentCatalog = catalog
            _seed_catalog_with_builtins(self._catalog)
        else:
            self._catalog = default_catalog()
        self._catalog_typed: AgentCatalog = self._catalog

    @property
    def catalog(self) -> AgentCatalog:
        return self._catalog

    @classmethod
    def from_config(
        cls,
        config: UnifiedConfig,
        *,
        catalog: AgentCatalog | None = None,
    ) -> AgentRegistry:
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
        self.agents[name] = config
        logger.debug("Registered agent: {}", name)
        support: object = getattr(config, "_support", None)
        if isinstance(support, AgentSupport):
            if self._catalog is not None and self._catalog.get(support.name) is None:
                self._catalog.add(support)
            return

        builtin = _find_builtin_support(name)
        if builtin is not None and self._catalog is not None:
            override_support = _synthesize_override_support(name, config, builtin)
            if self._catalog.get(name) is None:
                self._catalog.add(override_support)
            else:
                self._catalog.replace_builtin(name, override_support)
            object.__setattr__(config, "_support", override_support)

    def unregister(self, name: str) -> None:
        self.agents.pop(name, None)
        if self._catalog is not None:
            self._catalog.remove(name)

    def provision_vision_verdict_agent(
        self,
        *,
        workspace: Workspace | None = None,
        stack: ProjectStack | None = None,
    ) -> bool:
        registered = provision_vision_verdict_agent(
            self._catalog,
            workspace=workspace if workspace is not None else None,
            stack=stack if stack is not None else None,
        )
        if not registered:
            return False
        support = self._catalog.get("vision-verdict")
        if support is not None:
            self.agents["vision-verdict"] = support.config
        return True

    def get(self, name: str) -> AgentConfig | None:
        config = self.agents.get(name)
        if config is not None:
            return config
        return _resolve_dynamic_agent(
            name,
            self._ccs_defaults,
            base_lookup=self.agents.get,
        )

    def list_agents(self) -> list[str]:
        return list(self.agents.keys())

    def get_command(self, name: str) -> str | None:
        config = self.get(name)
        return config.cmd if config else None

    def validate(self) -> list[str]:
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
    segments = name.split("/")
    resolved: AgentConfig | None = None

    def _base(agent_name: str) -> AgentConfig | None:
        if base_lookup is not None:
            override = base_lookup(agent_name)
            if override is not None:
                return deepcopy(override)
        builtin = builtin_agents().get(agent_name)
        return deepcopy(builtin) if builtin is not None else None

    if name.startswith("codex/"):
        resolved = resolve_codex_alias(name, _base("codex"))
    elif name.startswith("pi/"):
        model_id = name.removeprefix("pi/")
        if len(segments) < _MIN_PI_SEGMENTS or not _is_valid_pi_model_id(model_id):
            return None

        base_config = _base("pi")
        if base_config is None:
            return None
        pi_overrides: dict[str, object] = {
            "model_flag": f"--model {shlex.quote(model_id)}",
        }
        resolved = base_config.model_copy(update=pi_overrides)
    elif name.startswith("cursor/"):
        if not _is_valid_cursor_model_id(name.removeprefix("cursor/")):
            return None
        model_id = name.removeprefix("cursor/")
        if model_id == "":
            return None

        base_config = _base("cursor")
        if base_config is None:
            return None
        cursor_overrides: dict[str, object] = {
            "model_flag": f"--model {shlex.quote(model_id)}",
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
    model_id = name.removeprefix("kimi/")
    if not _is_valid_kimi_model_id(model_id):
        return None

    base_config = base_lookup("kimi")
    if base_config is None:
        return None
    kimi_overrides: dict[str, object] = {
        "model_flag": f"-m {shlex.quote(model_id)}",
    }
    return base_config.model_copy(update=kimi_overrides)


def _resolve_dynamic_simple_prefixed_agent(
    name: str,
    segments: list[str],
    base_lookup: Callable[[str], AgentConfig | None],
) -> AgentConfig | None:
    if name.startswith("opencode/"):
        if len(segments) >= _MIN_OPENCODE_SEGMENTS and all(segments[1:]):
            base_config = base_lookup("opencode")
            if base_config is not None:
                model_id = _opencode_alias_model_id(name)
                return base_config.model_copy(
                    update={
                        "model_flag": f"-m {shlex.quote(model_id)}",
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
                return base_config.model_copy(update={"model_flag": model_flag})
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
            return base_config.model_copy(update={"model": model_id, "model_flag": model_flag})
    return None


def _parse_agy_alias(
    alias_value: str, *, models: frozenset[str] = _AGY_MODELS
) -> tuple[str, str | None] | None:
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
    return base_config.model_copy(
        update={
            "model_flag": f"--model {shlex.quote(segments[1])}",
            "model": segments[1],
        }
    )


def _opencode_alias_model_id(name: str) -> str:
    return name.removeprefix("opencode/")


def _normalize_nanocoder_provider_and_model(name: str) -> tuple[str, str | None]:
    parts = name.removeprefix("nanocoder/").split("/")
    provider = parts[0]
    model = "/".join(parts[1:]) if len(parts) > 1 else None
    return provider, model


def _is_valid_pi_model_id(model_id: str) -> bool:
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
    if not model_id or any(ch.isspace() for ch in model_id):
        return False
    return all(segment for segment in model_id.split("/"))


def _is_valid_kimi_model_id(model_id: str) -> bool:
    if not model_id or any(ch.isspace() for ch in model_id):
        return False
    if model_id.startswith("-"):
        return False
    if ":" in model_id:
        return False
    return all(segment for segment in model_id.split("/"))
