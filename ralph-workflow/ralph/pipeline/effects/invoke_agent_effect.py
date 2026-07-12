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
        requires_completion_evidence: Whether a clean exit must be corroborated
            by agent-side completion evidence (a submitted artifact or a
            ``declare_complete`` sentinel). Every pipeline phase requires it.
            The out-of-graph ``policy_remediation`` phase sets it False: it has
            no artifact contract, is not granted the ``artifact.submit``
            capability that exposes ``declare_complete``, and is judged by a
            deterministic validator that re-runs after the agent exits — so
            there is no evidence for the agent to leave, and none is trusted.
    """

    agent_name: str
    phase: PipelinePhase
    prompt_file: str
    drain: str | None = None
    chain_name: str = ""
    requires_completion_evidence: bool = True
