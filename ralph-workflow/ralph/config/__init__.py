"""Configuration models and enums for Ralph.

Use this package when you need to inspect or construct validated configuration
objects, or when you need the public enums used by CLI/config plumbing.

Typical entry points:

- ``ralph.config.loader.load_config`` to build the merged runtime config
- ``AgentConfig`` and ``UnifiedConfig`` for validated configuration objects
- ``Verbosity`` and related enums for CLI/config values
- ``ensure_global_config`` and friends to bootstrap user configs on first run
- ``load_optional_toml`` and ``validate_or_default`` for the resilient TOML
  fallback seam, ``emit_config_failure_warning`` for the shared
  configuration-failure warning recorder, and
  ``reset_fallback_warning_registry`` to clear its per-process
  deduplication registry

Public names resolve lazily, on first attribute access, from the module that
defines them. Importing a leaf submodule such as ``ralph.config.models`` no
longer pulls the bootstrap module (and through it the MCP stack) into the
importing process, which keeps every interpreter that touches the config
models cheap to start.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

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

#: Public name -> (defining module, attribute name in that module).
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "AgentConfig": ("ralph.config.models", "AgentConfig"),
    "BootstrapResult": ("ralph.config.bootstrap", "BootstrapResult"),
    "GeneralConfig": ("ralph.config.models", "GeneralConfig"),
    "JsonParserType": ("ralph.config.enums", "JsonParserType"),
    "PauseOnExit": ("ralph.config.enums", "PauseOnExit"),
    "RecoveryStrategy": ("ralph.config.enums", "RecoveryStrategy"),
    "UnifiedConfig": ("ralph.config.models", "UnifiedConfig"),
    "Verbosity": ("ralph.config.enums", "Verbosity"),
    "describe_config_failure": ("ralph.config._toml_fallback", "describe_config_failure"),
    "emit_config_failure_warning": (
        "ralph.config._toml_fallback",
        "emit_config_failure_warning",
    ),
    "emit_first_run_welcome": ("ralph.config.welcome", "emit_first_run_welcome"),
    "ensure_global_agents_config": (
        "ralph.config.bootstrap",
        "ensure_global_agents_config",
    ),
    "ensure_global_config": ("ralph.config.bootstrap", "ensure_global_config"),
    "ensure_global_mcp_config": ("ralph.config.bootstrap", "ensure_global_mcp_config"),
    "ensure_local_configs": ("ralph.config.bootstrap", "ensure_local_configs"),
    "load_optional_toml": ("ralph.config._toml_fallback", "load_optional_toml"),
    "regenerate_all": ("ralph.config.bootstrap", "regenerate_all"),
    "reset_fallback_warning_registry": (
        "ralph.config._toml_fallback",
        "reset_fallback_warning_registry",
    ),
    "resolve_global_config_dir": (
        "ralph.config.bootstrap",
        "resolve_global_config_dir",
    ),
    "validate_or_default": ("ralph.config._toml_fallback", "validate_or_default"),
}


def __getattr__(name: str) -> object:
    """Resolve a public export from its defining module on first access.

    The eager imports this replaces pulled ``ralph.config.bootstrap`` -- and
    through it the MCP startup stack -- into every process that only needed
    the configuration models.
    """
    try:
        module_name, attr_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg) from exc

    module = import_module(module_name)
    namespace = cast("dict[str, object]", module.__dict__)
    value = namespace[attr_name]
    cast("dict[str, object]", globals())[name] = value
    return value
