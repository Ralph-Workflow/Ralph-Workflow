from __future__ import annotations

from collections.abc import Callable

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.config._toml_fallback import (
        describe_config_failure,
        emit_config_failure_warning,
        load_optional_toml,
        reset_fallback_warning_registry,
        validate_or_default,
    )
    from ralph.config.bootstrap import (
        BootstrapResult,
        ensure_global_agents_config,
        ensure_global_config,
        ensure_global_mcp_config,
        ensure_local_configs,
        regenerate_all,
        resolve_global_config_dir,
    )
    from ralph.config.enums import (
        JsonParserType,
        PauseOnExit,
        RecoveryStrategy,
        Verbosity,
    )
    from ralph.config.models import (
        AgentConfig,
        GeneralConfig,
        UnifiedConfig,
    )
    from ralph.config.welcome import emit_first_run_welcome

__all__ = [
    "AgentConfig",
    "BootstrapResult",
    "GeneralConfig",
    "JsonParserType",
    "PauseOnExit",
    "RecoveryStrategy",
    "UnifiedConfig",
    "Verbosity",
    "describe_config_failure",
    "emit_config_failure_warning",
    "emit_first_run_welcome",
    "ensure_global_agents_config",
    "ensure_global_config",
    "ensure_global_mcp_config",
    "ensure_local_configs",
    "load_optional_toml",
    "regenerate_all",
    "reset_fallback_warning_registry",
    "resolve_global_config_dir",
    "validate_or_default",
]


def _load_AgentConfig() -> object:
    from ralph.config.models import AgentConfig
    return AgentConfig

def _load_BootstrapResult() -> object:
    from ralph.config.bootstrap import BootstrapResult
    return BootstrapResult

def _load_GeneralConfig() -> object:
    from ralph.config.models import GeneralConfig
    return GeneralConfig

def _load_JsonParserType() -> object:
    from ralph.config.enums import JsonParserType
    return JsonParserType

def _load_PauseOnExit() -> object:
    from ralph.config.enums import PauseOnExit
    return PauseOnExit

def _load_RecoveryStrategy() -> object:
    from ralph.config.enums import RecoveryStrategy
    return RecoveryStrategy

def _load_UnifiedConfig() -> object:
    from ralph.config.models import UnifiedConfig
    return UnifiedConfig

def _load_Verbosity() -> object:
    from ralph.config.enums import Verbosity
    return Verbosity

def _load_describe_config_failure() -> object:
    from ralph.config._toml_fallback import describe_config_failure
    return describe_config_failure

def _load_emit_config_failure_warning() -> object:
    from ralph.config._toml_fallback import emit_config_failure_warning
    return emit_config_failure_warning

def _load_emit_first_run_welcome() -> object:
    from ralph.config.welcome import emit_first_run_welcome
    return emit_first_run_welcome

def _load_ensure_global_agents_config() -> object:
    from ralph.config.bootstrap import ensure_global_agents_config
    return ensure_global_agents_config

def _load_ensure_global_config() -> object:
    from ralph.config.bootstrap import ensure_global_config
    return ensure_global_config

def _load_ensure_global_mcp_config() -> object:
    from ralph.config.bootstrap import ensure_global_mcp_config
    return ensure_global_mcp_config

def _load_ensure_local_configs() -> object:
    from ralph.config.bootstrap import ensure_local_configs
    return ensure_local_configs

def _load_load_optional_toml() -> object:
    from ralph.config._toml_fallback import load_optional_toml
    return load_optional_toml

def _load_regenerate_all() -> object:
    from ralph.config.bootstrap import regenerate_all
    return regenerate_all

def _load_reset_fallback_warning_registry() -> object:
    from ralph.config._toml_fallback import reset_fallback_warning_registry
    return reset_fallback_warning_registry

def _load_resolve_global_config_dir() -> object:
    from ralph.config.bootstrap import resolve_global_config_dir
    return resolve_global_config_dir

def _load_validate_or_default() -> object:
    from ralph.config._toml_fallback import validate_or_default
    return validate_or_default

_LOADERS: dict[str, Callable[[], object]] = {
    "AgentConfig": _load_AgentConfig,
    "BootstrapResult": _load_BootstrapResult,
    "GeneralConfig": _load_GeneralConfig,
    "JsonParserType": _load_JsonParserType,
    "PauseOnExit": _load_PauseOnExit,
    "RecoveryStrategy": _load_RecoveryStrategy,
    "UnifiedConfig": _load_UnifiedConfig,
    "Verbosity": _load_Verbosity,
    "describe_config_failure": _load_describe_config_failure,
    "emit_config_failure_warning": _load_emit_config_failure_warning,
    "emit_first_run_welcome": _load_emit_first_run_welcome,
    "ensure_global_agents_config": _load_ensure_global_agents_config,
    "ensure_global_config": _load_ensure_global_config,
    "ensure_global_mcp_config": _load_ensure_global_mcp_config,
    "ensure_local_configs": _load_ensure_local_configs,
    "load_optional_toml": _load_load_optional_toml,
    "regenerate_all": _load_regenerate_all,
    "reset_fallback_warning_registry": _load_reset_fallback_warning_registry,
    "resolve_global_config_dir": _load_resolve_global_config_dir,
    "validate_or_default": _load_validate_or_default,
}


def __getattr__(name: str) -> object:
    loader = _LOADERS.get(name)
    if loader is None:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    return loader()
