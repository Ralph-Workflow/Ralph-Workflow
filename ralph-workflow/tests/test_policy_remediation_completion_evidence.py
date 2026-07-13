"""The policy-remediation session requires no agent-side completion evidence.

Remediation has no artifact contract and its prompt never asks the agent to
call ``declare_complete`` — completion IS the absence of unresolved markers,
and the driver re-runs the deterministic validator after every attempt
(``ralph.project_policy.remediation.remediate``). The agent's claim was never
trusted, so the completion-enforcing transports (claude-interactive, agy,
cursor, pi) must treat a clean exit as terminal here instead of raising
"agent exited without required completion evidence".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from ralph.agents.execution_state import strategy_for_transport
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    AgentInvocationError,
    CompletionCheckOptions,
    check_process_result,
)
from ralph.config.enums import AgentTransport
from ralph.pipeline.effects import InvokeAgentEffect
from tests.fake_handle import _FakeHandle

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.process.manager import ManagedProcess


def _zero_grace() -> TimeoutPolicy:
    return TimeoutPolicy(idle_timeout_seconds=None, parent_exit_grace_seconds=0.0)


def test_clean_exit_is_terminal_when_completion_evidence_is_not_required(
    tmp_path: Path,
) -> None:
    """No artifact, no declare_complete, no sentinel — and no error raised."""
    check_process_result(
        cast("ManagedProcess", _FakeHandle(returncode=0)),
        "agy",
        [],
        CompletionCheckOptions(
            execution_strategy=strategy_for_transport(AgentTransport.AGY),
            workspace_path=tmp_path,
            required_artifact=None,
            requires_completion_evidence=False,
            policy=_zero_grace(),
        ),
    )


def test_clean_exit_still_raises_when_completion_evidence_is_required(
    tmp_path: Path,
) -> None:
    """The default contract is unchanged: evidence is required unless opted out."""
    with pytest.raises(AgentInvocationError):
        check_process_result(
            cast("ManagedProcess", _FakeHandle(returncode=0)),
            "agy",
            [],
            CompletionCheckOptions(
                execution_strategy=strategy_for_transport(AgentTransport.AGY),
                workspace_path=tmp_path,
                required_artifact=None,
                policy=_zero_grace(),
            ),
        )


def test_invoke_agent_effect_requires_completion_evidence_by_default() -> None:
    effect = InvokeAgentEffect(
        agent_name="claude",
        phase="development",
        prompt_file="prompt.md",
    )

    assert effect.requires_completion_evidence is True


def test_remediation_effect_opts_out_of_completion_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production remediation closure builds an evidence-free effect."""
    from ralph.cli.commands._load_result import _LoadResult
    from ralph.config.models import UnifiedConfig
    from ralph.display.context import make_display_context
    from ralph.pipeline import effect_executor as effect_executor_module
    from ralph.pipeline.events import PipelineEvent
    from ralph.pipeline.state import PipelineState
    from ralph.policy.loader import default_dir, load_policy
    from ralph.project_policy import cli_integration
    from ralph.project_policy.pipeline_graph import PHASE_ANALYSIS, PHASE_REMEDIATION
    from ralph.workspace.scope import WorkspaceScope

    observed: list[InvokeAgentEffect] = []

    def fake_execute_agent_effect(
        effect: InvokeAgentEffect,
        _config: object,
        _pipeline_deps: object,
        _workspace_scope: object,
        **_opts: object,
    ) -> object:
        observed.append(effect)
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        effect_executor_module, "execute_agent_effect", fake_execute_agent_effect
    )

    load_result = _LoadResult(
        config=UnifiedConfig(),
        workspace_scope=WorkspaceScope(
            root="/test/project", allowed_roots=["/test/project"]
        ),
        initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
        policy_bundle=load_policy(default_dir()),
        run_id="test-run-id",
    )
    invoke = cli_integration._make_production_invoke_agent(
        load_result,
        cast("object", object()),
        load_result.workspace_scope,
        None,
        make_display_context(),
    )

    assert invoke(phase=PHASE_REMEDIATION, prompt_path="prompt.md") is True
    assert observed and observed[0].requires_completion_evidence is False

    # The ANALYSIS phase is the mirror image: returning a decision artifact is
    # its entire purpose, so its completion evidence IS required.
    observed.clear()
    assert invoke(phase=PHASE_ANALYSIS, prompt_path="prompt.md") is True
    assert observed and observed[0].requires_completion_evidence is True
