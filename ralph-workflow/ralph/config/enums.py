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
"""Type alias for a pipeline phase identifier.

A pipeline phase name is a plain ``str`` loaded at runtime from the
``pipeline.toml`` configuration file. The runtime no longer hard-codes
the set of legal phase names; phase order, dependencies, and
gate-conditions are all declared in configuration and validated by
:func:`ralph.config.pipeline.load_pipeline_definition`. Built-in
phase handlers expose their canonical names as module-level
constants (e.g. ``DEFAULT_*_PHASE`` in :mod:`ralph.phases`) so call
sites that need a known-good value can reference the constant
directly instead of repeating the string literal.

Use this alias for type annotations on helpers, gate conditions, and
phase-handler signatures that take or return a phase name. Treat the
value as opaque; do not pattern-match on string contents because the
set of legal phases is configurable and can change between workspaces.

Examples:
    >>> def handle(phase: PipelinePhase) -> None: ...  # doctest: +SKIP

See also:
    :mod:`ralph.phases` ships the canonical phase-name constants.
    :func:`ralph.config.pipeline.load_pipeline_definition` reads
    ``pipeline.toml`` and returns the validated phase set for the
    active workspace.
"""
