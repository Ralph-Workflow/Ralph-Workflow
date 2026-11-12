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
    multimodal_requested: bool = False
    multimodal_fixture_size: tuple[int, int] | None = None
    require_multimodal_perception_secret: bool = True
    display: ParallelDisplay | None = None
    support_resolver: Callable[[str], AgentSupport | None] | None = None
