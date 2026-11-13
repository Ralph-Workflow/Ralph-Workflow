from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.execution_state import strategy_for_transport
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    AgentInvocationError,
    CompletionCheckOptions,
    check_process_result,
)
from ralph.cli.commands._load_result import _LoadResult
from ralph.config.enums import AgentTransport
from ralph.config.models import UnifiedConfig
from ralph.display.context import DisplayContext, make_display_context
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import default_dir, load_policy
from ralph.project_policy import cli_integration
from ralph.project_policy.pipeline_graph import PHASE_ANALYSIS, PHASE_REMEDIATION
from ralph.workspace.scope import WorkspaceScope
from tests.fake_handle import _FakeHandle

if TYPE_CHECKING:
    from pathlib import Path


def _zero_grace() -> TimeoutPolicy:
    return TimeoutPolicy(idle_timeout_seconds=None, parent_exit_grace_seconds=0.0)


def test_clean_exit_is_terminal_when_completion_evidence_is_not_required(
    tmp_path: Path,
) -> None:
    check_process_result(
        _FakeHandle(returncode=0),
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
    with pytest.raises(AgentInvocationError):
        check_process_result(
            _FakeHandle(returncode=0),
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


@pytest.fixture(scope="module")
def _remediation_invoke_inputs() -> tuple[_LoadResult, DisplayContext]:
    return (
        _LoadResult(
            config=UnifiedConfig(),
            workspace_scope=WorkspaceScope(root="/test/project", allowed_roots=["/test/project"]),
            initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
            policy_bundle=load_policy(default_dir()),
            run_id="test-run-id",
        ),
        make_display_context(),
    )


def test_remediation_effect_opts_out_of_completion_evidence(
    monkeypatch: pytest.MonkeyPatch,
    _remediation_invoke_inputs: tuple[_LoadResult, DisplayContext],
) -> None:
    observed: list[InvokeAgentEffect] = []

    def fake_execute_agent_effect(
        effect: InvokeAgentEffect,
        config: object,
        pipeline_deps: object,
        workspace_scope: object,
        **_opts: object,
    ) -> object:
        observed.append(effect)
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(effect_executor_module, "execute_agent_effect", fake_execute_agent_effect)

    load_result, display_context = _remediation_invoke_inputs
    invoke = cli_integration._make_production_invoke_agent(
        load_result,
        object(),
        load_result.workspace_scope,
        None,
        display_context,
    )

    assert invoke(phase=PHASE_REMEDIATION, prompt_path="prompt.md") is True
    assert observed and observed[0].requires_completion_evidence is False

    observed.clear()
    assert invoke(phase=PHASE_ANALYSIS, prompt_path="prompt.md") is True
    assert observed and observed[0].requires_completion_evidence is True
