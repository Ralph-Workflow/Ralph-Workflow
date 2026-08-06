"""Grouped parameters for a smoke run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.agents.invoke import InvokeOptions
    from ralph.agents.support import AgentSupport
    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.mcp.server.lifecycle import SessionBridgeLike
    from ralph.pipeline.factory import PipelineDeps


@dataclass(frozen=True)
class SmokeRunParams:
    """Grouped parameters for a smoke run."""

    agent_name: str
    config: AgentConfig
    unified_config: UnifiedConfig
    workspace_root: Path
    prompt_file: Path
    output_file: Path
    options: InvokeOptions
    display_context: DisplayContext
    bridge: SessionBridgeLike | None = None
    pipeline_deps: PipelineDeps | None = None
    subagents_requested: bool = False
    #: Optional fresh ``ParallelDisplay`` instance the smoke plumbing owns
    #: for the duration of the run. When set, the per-instance
    #: :class:`CapabilityObservationRecorder` is snapshotted between
    #: ``_execute_smoke_turns`` and ``_detect_smoke_errors`` so the
    #: capability-breaks detector sees what the display actually
    #: rendered. Defaults to ``None`` so existing test constructions
    #: (which never need a display) keep passing.
    display: ParallelDisplay | None = None
    #: Optional callable that resolves the registered :class:`AgentSupport`
    #: for an agent name. When ``None``, ``_run_smoke_agent`` resolves
    #: via ``AgentRegistry.from_config(params.unified_config).catalog.get(name)``
    #: so production callers get the synthesised ``AgentSupport`` for
    #: dynamic aliases (e.g. ``opencode/minimax/MiniMax-M3``) without
    #: any extra wiring. Tests inject their own resolver to feed a
    #: synthetic support without standing up a registry.
    support_resolver: Callable[[str], AgentSupport | None] | None = None
