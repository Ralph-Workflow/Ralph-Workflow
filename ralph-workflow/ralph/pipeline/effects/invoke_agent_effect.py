"""Invoke-agent pipeline effect."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.config.enums import PipelinePhase
else:
    PipelinePhase = import_module("ralph.config.enums").PipelinePhase


@dataclass(frozen=True)
class InvokeAgentEffect:
    """Effect to invoke an AI agent.

    Attributes:
        agent_name: Name of the agent to invoke.
        phase: Current pipeline phase.
        prompt_file: Path to the prompt file for the agent.
        chain_name: Name of the agent chain being used.
    """

    agent_name: str
    phase: PipelinePhase
    prompt_file: str
    drain: str | None = None
    chain_name: str = ""
