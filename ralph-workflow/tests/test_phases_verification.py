"""Tests for ralph/phases/verification.py — verification phase handler."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

if TYPE_CHECKING:
    from pathlib import Path

import loguru

from ralph.phases import PhaseContext, get_handler, register_role_handlers
from ralph.phases.verification import handle_verification_phase
from ralph.pipeline.effects import InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent
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


class TestVerificationArtifactGate:
    def test_artifact_gate_passes_when_present_and_nonempty(self, tmp_path: Path) -> None:
        drain = "gate"
        verification = PhaseVerificationPolicy(
            kind="artifact",
            gate_for="advancement",
            on_failure_route=None,
        )
        ctx = _make_context(tmp_path, drain, verification)
        artifact_path = tmp_path / ".agent" / "artifacts" / f"{drain}_verification.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text('{"verified": true}', encoding="utf-8")

        result = handle_verification_phase(_invoke_effect(drain), ctx)
        assert result == [PipelineEvent.AGENT_SUCCESS]

    def test_artifact_gate_fails_when_missing(self, tmp_path: Path) -> None:
        drain = "gate"
        verification = PhaseVerificationPolicy(
            kind="artifact",
            gate_for="advancement",
            on_failure_route=None,
        )
        ctx = _make_context(tmp_path, drain, verification)

        result = handle_verification_phase(_invoke_effect(drain), ctx)
        assert len(result) == 1
        event = result[0]
        assert isinstance(event, PhaseFailureEvent)
        assert event.recoverable is False
        assert "Missing required verification artifact" in event.reason

    def test_artifact_gate_fails_when_empty(self, tmp_path: Path) -> None:
        drain = "gate"
        verification = PhaseVerificationPolicy(
            kind="artifact",
            gate_for="advancement",
            on_failure_route=None,
        )
        ctx = _make_context(tmp_path, drain, verification)
        artifact_path = tmp_path / ".agent" / "artifacts" / f"{drain}_verification.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text("", encoding="utf-8")

        result = handle_verification_phase(_invoke_effect(drain), ctx)
        assert len(result) == 1
        event = result[0]
        assert isinstance(event, PhaseFailureEvent)
        assert event.recoverable is False
        assert "is empty" in event.reason

    def test_artifact_gate_uses_on_failure_route(self, tmp_path: Path) -> None:
        drain = "gate"
        verification = PhaseVerificationPolicy(
            kind="artifact",
            gate_for="advancement",
            on_failure_route="fix",
        )
        ctx = _make_context(tmp_path, drain, verification)

        result = handle_verification_phase(_invoke_effect(drain), ctx)
        assert len(result) == 1
        event = result[0]
        assert isinstance(event, PhaseFailureEvent)
        assert event.recoverable is False


class TestVerificationMakeTargetKind:
    def test_make_target_kind_stub_returns_failure(self, tmp_path: Path) -> None:
        drain = "gate"
        verification = PhaseVerificationPolicy(
            kind="make_target",
            gate_for="advancement",
            on_failure_route=None,
        )
        ctx = _make_context(tmp_path, drain, verification)

        result = handle_verification_phase(_invoke_effect(drain), ctx)
        assert len(result) == 1
        event = result[0]
        assert isinstance(event, PhaseFailureEvent)
        assert event.recoverable is False
        assert "make_target verification is declared but not yet executable" in event.reason


class TestVerificationNoneKind:
    def test_none_kind_passes_through(self, tmp_path: Path) -> None:
        drain = "gate"
        verification = PhaseVerificationPolicy(
            kind="none",
            gate_for="advancement",
            on_failure_route=None,
        )
        ctx = _make_context(tmp_path, drain, verification)

        result = handle_verification_phase(_invoke_effect(drain), ctx)
        assert result == [PipelineEvent.AGENT_SUCCESS]


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


class TestVerificationRoleDispatch:
    def test_handler_dispatches_via_role_not_phase_name(self) -> None:
        policy = PipelinePolicy(
            phases={
                "gate": PhaseDefinition(
                    drain="gate",
                    role="verification",
                    verification=PhaseVerificationPolicy(
                        kind="none",
                        gate_for="advancement",
                    ),
                    transitions=PhaseTransition(on_success="done"),
                ),
                "done": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(on_success="done"),
                ),
            },
            entry_phase="gate",
            terminal_phase="done",
        )
        register_role_handlers(policy)
        handler = get_handler("gate")
        assert handler is handle_verification_phase
