"""Backward-compatible enum re-exports for ralph configuration.

NOTE: PipelinePhase is now a type alias to str (not a StrEnum).
Phase names are loaded from pipeline.toml at startup. Well-known phases
are exposed as module-level constants for use in built-in phase handlers.
"""

from __future__ import annotations

from ralph.config.agent_transport import AgentTransport
from ralph.config.json_parser_type import JsonParserType
from ralph.config.pause_on_exit import PauseOnExit
from ralph.config.recovery_strategy import RecoveryStrategy
from ralph.config.verbosity import Verbosity

__all__ = [
    "AgentTransport",
    "JsonParserType",
    "PauseOnExit",
    "PipelinePhase",
    "RecoveryStrategy",
    "Verbosity",
]


# ---------------------------------------------------------------------------
# Pipeline phase type alias — phases come from pipeline.toml, not a fixed enum
# ---------------------------------------------------------------------------

PipelinePhase = str
