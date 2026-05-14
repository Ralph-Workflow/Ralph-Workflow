"""Configuration models and enums for Ralph.

Use this package when you need to inspect or construct validated configuration
objects, or when you need the public enums used by CLI/config plumbing.

Typical entry points:

- ``ralph.config.loader.load_config`` to build the merged runtime config
- ``AgentConfig`` and ``UnifiedConfig`` for validated configuration objects
- ``Verbosity`` and related enums for CLI/config values
- ``ensure_global_config`` and friends to bootstrap user configs on first run
"""

from ralph.config.bootstrap import (
    BootstrapResult,
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
    "emit_first_run_welcome",
    "ensure_global_config",
    "ensure_global_mcp_config",
    "ensure_local_configs",
    "regenerate_all",
    "resolve_global_config_dir",
]
