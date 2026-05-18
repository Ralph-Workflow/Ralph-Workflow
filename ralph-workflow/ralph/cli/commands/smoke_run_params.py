"""Grouped parameters for a smoke run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.agents.invoke import InvokeOptions
    from ralph.config.models import AgentConfig
    from ralph.display.context import DisplayContext


@dataclass(frozen=True)
class SmokeRunParams:
    """Grouped parameters for a smoke run."""

    agent_name: str
    config: AgentConfig
    workspace_root: Path
    prompt_file: Path
    output_file: Path
    options: InvokeOptions
    display_context: DisplayContext
