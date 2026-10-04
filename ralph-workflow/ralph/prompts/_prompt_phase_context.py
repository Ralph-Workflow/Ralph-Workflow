from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.policy.models import PipelinePolicy
    from ralph.prompts.types import SessionCapabilities
    from ralph.workspace.protocol import Workspace


@dataclass(frozen=True)
class PromptPhaseContext:
    """Required inputs for prompt materialization: the phase, workspace, and policy bindings."""

    phase: str
    workspace: Workspace
    pipeline_policy: PipelinePolicy
    session_caps: SessionCapabilities
    workspace_root: Path
    # S-5: the executing agent transport (e.g. AgentTransport.CODEX). The
    # materialization passes it into ``DeveloperPromptInputs.transport`` so
    # the shared subagent partial can branch on HAS_SUBAGENTS in production;
    # ``None`` falls through to the empty-string sequential path.
    transport: object = None
