from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.console import Console

    from ralph.agents.chain import ChainManager
    from ralph.agents.registry import AgentRegistry
    from ralph.config.models import UnifiedConfig
    from ralph.policy.models import (
        AgentsPolicy,
        ArtifactsPolicy,
        PipelinePolicy,
    )
    from ralph.workspace.probe import WorkspaceChangeProbe
    from ralph.workspace.protocol import Workspace


@dataclass(frozen=True)
class PhaseContext:
    workspace: Workspace
    registry: AgentRegistry
    chain_manager: ChainManager
    pipeline_policy: PipelinePolicy
    agents_policy: AgentsPolicy
    artifacts_policy: ArtifactsPolicy
    config: UnifiedConfig | None = None
    console: Console | None = None
    workspace_probe: WorkspaceChangeProbe | None = None

    @classmethod
    def construct(
        cls,
        *,
        workspace: Workspace,
        registry: AgentRegistry,
        chain_manager: ChainManager,
        pipeline_policy: PipelinePolicy,
        agents_policy: AgentsPolicy,
        artifacts_policy: ArtifactsPolicy,
        config: UnifiedConfig | None = None,
        console: Console | None = None,
        workspace_probe: WorkspaceChangeProbe | None = None,
    ) -> PhaseContext:
        return cls(
            workspace=workspace,
            registry=registry,
            chain_manager=chain_manager,
            pipeline_policy=pipeline_policy,
            agents_policy=agents_policy,
            artifacts_policy=artifacts_policy,
            config=config,
            console=console,
            workspace_probe=workspace_probe,
        )

    @classmethod
    def model_construct(
        cls,
        *,
        workspace: Workspace,
        registry: AgentRegistry,
        chain_manager: ChainManager,
        pipeline_policy: PipelinePolicy,
        agents_policy: AgentsPolicy,
        artifacts_policy: ArtifactsPolicy,
        config: UnifiedConfig | None = None,
        console: Console | None = None,
        workspace_probe: WorkspaceChangeProbe | None = None,
    ) -> PhaseContext:
        return cls.construct(
            workspace=workspace,
            registry=registry,
            chain_manager=chain_manager,
            pipeline_policy=pipeline_policy,
            agents_policy=agents_policy,
            artifacts_policy=artifacts_policy,
            config=config,
            console=console,
            workspace_probe=workspace_probe,
        )
