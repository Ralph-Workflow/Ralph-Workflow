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
    #: Set to True to drive this smoke run from the multimodal scenario.
    #: When True, ``multimodal_requested`` is propagated to the prompt
    #: builder (appending the multimodal contract bullets),
    #: ``run_smoke_plumbing`` materializes the deterministic PNG
    #: fixture and the ``[media] max_inline_bytes`` mcp.toml fragment
    #: before the turns start, and ``_detect_smoke_errors`` appends a
    #: specific named break when the agent did not produce a verified
    #: ``read_media`` / ``read_image`` call. Defaults to False so every
    #: existing smoke run is unchanged.
    multimodal_requested: bool = False
    #: The fixture size chosen for this run (only meaningful when
    #: ``multimodal_requested``); persisted on
    #: :class:`~ralph.pipeline.plumbing.smoke_plumbing.SmokeRunResult`
    #: so the operator report and the run's evidence row can cite the
    #: same geometry the grader recomputes the sha256 over.
    multimodal_fixture_size: tuple[int, int] | None = None
    #: CCS receives text replay handles rather than image bytes, so its
    #: smoke contract proves handle replay and metadata without requiring
    #: pixel-only perception evidence.
    require_multimodal_perception_secret: bool = True
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
