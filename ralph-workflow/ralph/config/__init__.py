"""Configuration models and enums for Ralph.

Use this package when you need to inspect or construct validated configuration
objects, or when you need the public enums used by CLI/config plumbing.

Typical entry points:

- ``ralph.config.loader.load_config`` to build the merged runtime config
- ``AgentConfig`` and ``UnifiedConfig`` for validated configuration objects
- ``Verbosity``, ``ReviewDepth``, and related enums for CLI/config values
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
    ReviewDepth,
    Verbosity,
)
from ralph.config.models import (
    AgentConfig,
    CloudConfig,
    GeneralConfig,
    UnifiedConfig,
)

__all__ = [
    "AgentConfig",
    "BootstrapResult",
    "CloudConfig",
    "GeneralConfig",
    "JsonParserType",
    "PauseOnExit",
    "RecoveryStrategy",
    "ReviewDepth",
    "UnifiedConfig",
    "Verbosity",
    "ensure_global_config",
    "ensure_global_mcp_config",
    "ensure_local_configs",
    "regenerate_all",
    "resolve_global_config_dir",
]
