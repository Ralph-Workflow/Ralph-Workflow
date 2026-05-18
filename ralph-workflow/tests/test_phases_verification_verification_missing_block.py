"""Tests for ralph/phases/verification.py — verification phase handler."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

if TYPE_CHECKING:
    from pathlib import Path

import loguru

from ralph.phases import PhaseContext
from ralph.phases.verification import handle_verification_phase
from ralph.pipeline.effects import InvokeAgentEffect
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


class TestVerificationMissingBlock:
    def test_no_verification_block_logs_warning_and_passes(self, tmp_path: Path) -> None:
        drain = "gate"
        ctx = _make_context(tmp_path, drain, verification=None)

        messages: list[str] = []

        def _capture(m: object) -> None:
            messages.append(str(m))

        handler_id = loguru.logger.add(_capture, level="WARNING", format="{message}")
        try:
            result = handle_verification_phase(_invoke_effect(drain), ctx)
        finally:
            loguru.logger.remove(handler_id)

        assert result == [PipelineEvent.AGENT_SUCCESS]
        assert any("no verification policy" in m.lower() for m in messages)
