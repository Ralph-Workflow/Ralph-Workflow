"""Tests for ralph/phases/verification.py — verification phase handler."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

if TYPE_CHECKING:
    from pathlib import Path


from ralph.phases import PhaseContext
from ralph.phases.verification import handle_verification_phase
from ralph.pipeline.effects import InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import PipelineEvent
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PhaseVerificationPolicy,
    PipelinePolicy,
)
from ralph.workspace.fs import FsWorkspace


def _make_pipeline_policy(
    drain: str,
    verification: PhaseVerificationPolicy | None,
) -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            drain: PhaseDefinition(
                drain=drain,
                role="verification",
                verification=verification,
                transitions=PhaseTransition(on_success="done"),
            ),
            "done": PhaseDefinition(
                drain="complete",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done"),
            ),
        },
        entry_phase=drain,
        terminal_phase="done",
    )


def _make_context(
    tmp_path: Path,
    drain: str,
    verification: PhaseVerificationPolicy | None,
) -> PhaseContext:
    workspace = FsWorkspace(tmp_path)
    pipeline_policy = _make_pipeline_policy(drain, verification)
    agents_policy = AgentsPolicy(
        agent_chains={drain: AgentChainConfig(agents=["claude"])},
        agent_drains={
            drain: AgentDrainConfig(chain=drain),
            "complete": AgentDrainConfig(chain=drain),
        },
    )
    return PhaseContext.construct(
        workspace=workspace,
        registry=MagicMock(),
        chain_manager=MagicMock(),
        pipeline_policy=pipeline_policy,
        agents_policy=agents_policy,
        artifacts_policy=ArtifactsPolicy(),
    )


def _invoke_effect(drain: str) -> InvokeAgentEffect:
    return InvokeAgentEffect(agent_name="claude", phase=drain, prompt_file="prompt.txt")


class TestVerificationPreparePromptEffect:
    def test_prepare_prompt_effect_returns_prompt_prepared(self, tmp_path: Path) -> None:
        drain = "gate"
        verification = PhaseVerificationPolicy(
            kind="artifact",
            gate_for="advancement",
            on_failure_route=None,
        )
        ctx = _make_context(tmp_path, drain, verification)
        effect = MagicMock(spec=PreparePromptEffect)
        effect.iteration = 1

        result = handle_verification_phase(effect, ctx)
        assert result == [PipelineEvent.PROMPT_PREPARED]
