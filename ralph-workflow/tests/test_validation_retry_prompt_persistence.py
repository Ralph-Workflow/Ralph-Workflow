"""Regression coverage for S-2 validation retry-prompt persistence."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from ralph.mcp.tools.artifact import ArtifactHandlerDeps
from ralph.mcp.tools.md_artifact import handle_submit_md_artifact
from ralph.phases import PhaseContext
from ralph.phases.required_artifacts import retry_hint_path
from ralph.phases.verification import handle_verification_phase
from ralph.pipeline.effect_executor import write_agent_retry_prompt
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.policy.loader import load_policy
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
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.memory import MemoryWorkspace
from tests._tool_artifact_2_helper_memorybackend import MemoryBackend
from tests._tool_artifact_2_helper_mocksession import MockSession
from tests._tool_artifact_2_helper_mockworkspace import MockWorkspace

_INVALID_PRODUCT_SPEC = "---\ntype: product_spec\n---\n"
_VALID_PRODUCT_SPEC = """---
type: product_spec
---
## Title
- [T1] Retry persistence
## Scope
- [S1] Retain validation context until recovery
## Goals
- [G1] Keep retry prompts actionable
## Users
- [U1] Pipeline agents
## Success Criteria
- [C1] Recovery clears stale validation context
"""


def _payload(result: object) -> dict[str, object]:
    return json.loads(result.content[0].text)


def _materialize_planning_prompt(workspace: MemoryWorkspace | FsWorkspace, tmp_path: Path) -> str:
    policy = load_policy(tmp_path / ".agent")
    workspace.write("PROMPT.md", "Repair the retained plan artifact.")
    workspace.write(".agent/artifacts/plan.md", "# Existing plan\n")
    return workspace.read(
        materialize_prompt_for_phase(
            PromptPhaseContext(
                phase="planning",
                workspace=workspace,
                pipeline_policy=policy.pipeline,
                session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
                workspace_root=tmp_path,
            ),
            PromptPhaseOptions(artifacts_policy=policy.artifacts, previous_phase="planning"),
        )
    )


def _verification_context(tmp_path: Path) -> tuple[PhaseContext, InvokeAgentEffect]:
    verification = PhaseVerificationPolicy(
        kind="artifact",
        gate_for="advancement",
        on_failure_route=None,
    )
    pipeline_policy = PipelinePolicy(
        phases={
            "gate": PhaseDefinition(
                drain="development",
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
        entry_phase="gate",
        terminal_phase="done",
    )
    agents_policy = AgentsPolicy(
        agent_chains={"development": AgentChainConfig(agents=["claude"])},
        agent_drains={
            "development": AgentDrainConfig(chain="development"),
            "complete": AgentDrainConfig(chain="development"),
        },
    )
    workspace = MemoryWorkspace(root=str(tmp_path))
    return (
        PhaseContext.construct(
            workspace=workspace,
            registry=MagicMock(),
            chain_manager=MagicMock(),
            pipeline_policy=pipeline_policy,
            agents_policy=agents_policy,
            artifacts_policy=ArtifactsPolicy(),
        ),
        InvokeAgentEffect(agent_name="claude", phase="gate", prompt_file="prompt.txt"),
    )


def test_regression_s2_two_failures_materialize_twice_with_accumulated_escalation(
    tmp_path: Path,
) -> None:
    """S-2: failure -> materialize -> failure -> re-materialize retains both attempts."""
    session = MockSession(drain="planning")
    workspace = MockWorkspace(tmp_path)
    filesystem_workspace = FsWorkspace(tmp_path)

    first = handle_submit_md_artifact(
        session,
        workspace,
        {"artifact_type": "product_spec", "content": _INVALID_PRODUCT_SPEC},
    )
    assert first.is_error is True
    hint_path = tmp_path / retry_hint_path("planning")
    assert hint_path.is_file()

    first_prompt = _materialize_planning_prompt(filesystem_workspace, tmp_path)
    assert "ATTEMPT 1" in first_prompt
    assert hint_path.exists()

    second = handle_submit_md_artifact(
        session,
        workspace,
        {"artifact_type": "product_spec", "content": "---\ntype: product_spec\n---\n## Title\n"},
    )
    assert second.is_error is True
    assert "ATTEMPT 1" in (second_hint := hint_path.read_text(encoding="utf-8"))
    assert "ATTEMPT 2" in second_hint
    second_prompt = _materialize_planning_prompt(filesystem_workspace, tmp_path)

    assert "ATTEMPT 1" in second_prompt
    assert "ATTEMPT 2" in second_prompt
    assert "THIS VALIDATION HAS FAILED 2 TIMES" in second_prompt


def test_regression_s2_inactivity_retry_after_materialization_keeps_validation_context(
    tmp_path: Path,
) -> None:
    """S-2: consuming a prompt must not hide validation context from inactivity recovery."""
    hint = "ATTEMPT 1\nSPEC008 at line 1, section Title: missing list items"
    workspace = FsWorkspace(tmp_path)
    workspace.write(retry_hint_path("planning"), hint)
    _materialize_planning_prompt(workspace, tmp_path)
    prompt_path = tmp_path / "PROMPT.md"
    prompt_path.write_text("Repair the planning artifact.", encoding="utf-8")

    retry_prompt = write_agent_retry_prompt(
        workspace_root=tmp_path,
        prompt_file=str(prompt_path),
        reason="InactivityTimeout",
        context_lines=["No tool activity observed."],
        drain="planning",
    )

    assert hint in Path(retry_prompt).read_text(encoding="utf-8")


def test_regression_s2_out_of_band_repair_then_phase_gate_clears_validation_hint(
    tmp_path: Path,
) -> None:
    """S-2: a repaired artifact accepted by its gate clears the active retry hint."""
    context, effect = _verification_context(tmp_path)
    hint_path = retry_hint_path("development")
    context.workspace.write(hint_path, "ATTEMPT 1\nSPEC008: repair required")
    context.workspace.write(
        ".agent/artifacts/development_verification.md",
        "## Verification\n\nRepaired outside the MCP submission path.\n",
    )

    assert handle_verification_phase(effect, context) == [PipelineEvent.AGENT_SUCCESS]
    assert not context.workspace.exists(hint_path)


def test_regression_s2_successful_submit_reports_recovery_and_clears_hint(tmp_path: Path) -> None:
    """S-2: failed submission followed by success reports recovery and clears context."""
    session = MockSession(drain="development")
    workspace = MockWorkspace(tmp_path)
    backend = MemoryBackend()
    deps = ArtifactHandlerDeps(backend=backend)
    hint_path = tmp_path / retry_hint_path("development")

    failed = handle_submit_md_artifact(
        session,
        workspace,
        {"artifact_type": "product_spec", "content": _INVALID_PRODUCT_SPEC},
        deps=deps,
    )
    assert failed.is_error is True
    assert backend.exists(hint_path)

    recovered = handle_submit_md_artifact(
        session,
        workspace,
        {"artifact_type": "product_spec", "content": _VALID_PRODUCT_SPEC},
        deps=deps,
    )

    assert recovered.is_error is False
    assert _payload(recovered)["validation_recovered"] is True
    assert _payload(recovered)["message"] == "VALIDATION RECOVERED"
    assert not backend.exists(hint_path)
