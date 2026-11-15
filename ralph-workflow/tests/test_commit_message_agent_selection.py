from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

import ralph.cli.commands.commit as commit_cli
from ralph.agents.registry import AgentRegistry
from ralph.agents.timeout_clock import FakeClock
from ralph.cli.commands._commit_chain_config import CommitChainConfig
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.plumbing import commit_plumbing
from ralph.policy.models import AgentsPolicy
from ralph.recovery.agent_unavailability_tracker import AgentUnavailabilityTracker
from ralph.recovery.unavailability_reason import UnavailabilityReason
from tests._pipeline_deps_factory import make_test_pipeline_deps


def test_commit_message_selection_regression_wu_c2_skips_cooled_first_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock(start=0.0)
    unavailable = AgentUnavailabilityTracker(clock=clock)
    unavailable.mark_unavailable(
        "commit",
        "cooled",
        UnavailabilityReason.NO_OUTPUT_AT_START,
    )
    config = UnifiedConfig(
        general=GeneralConfig(max_same_agent_retries=0),
        agents={
            "cooled": AgentConfig(cmd="cooled"),
            "eligible": AgentConfig(cmd="eligible"),
        },
    )
    registry = AgentRegistry.from_config(config)
    display_context = make_display_context()
    pipeline_deps = dataclasses.replace(
        make_test_pipeline_deps(
            display_context,
            registry_factory=lambda _config: registry,
            recovery_clock=clock,
        ),
        unavailability_store=unavailable,
    )
    invoked: list[str] = []

    def invoke_selected_agent(
        effect: object,
        *_args: object,
        **_kwargs: object,
    ) -> PipelineEvent:
        assert isinstance(effect, InvokeAgentEffect)
        invoked.append(effect.agent_name)
        return PipelineEvent.AGENT_SUCCESS

    def delete_artifacts(_root: Path) -> None:
        return None

    def read_artifact(_root: Path) -> str:
        return "fix(commit): select an eligible message agent"

    def write_prompt(_root: Path, _prompt: str) -> str:
        return str(tmp_path / "commit-prompt.md")

    monkeypatch.setattr(commit_plumbing, "execute_agent_effect", invoke_selected_agent)
    monkeypatch.setattr(commit_cli, "delete_commit_message_artifacts", delete_artifacts)
    monkeypatch.setattr(
        commit_cli,
        "read_commit_message_artifact",
        read_artifact,
    )
    monkeypatch.setattr(
        commit_cli,
        "write_commit_prompt_file",
        write_prompt,
    )

    chain_config = CommitChainConfig(
        registry=registry,
        agents=["cooled", "eligible"],
        verbose=False,
        agents_policy=AgentsPolicy(),
        general_config=config,
    )
    result = commit_plumbing.run_commit_plumbing(
        diff="diff --git a/file.txt b/file.txt",
        repo_root=tmp_path,
        chain_config=chain_config,
        display_context=display_context,
        pipeline_deps=pipeline_deps,
    )

    assert result.message == "fix(commit): select an eligible message agent"
    assert result.outcome is commit_plumbing.CommitAgentOutcome.COMPLETED
    assert invoked == ["eligible"]


def test_commit_message_retry_regression_wu_c2_reauthorizes_after_missing_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock(start=0.0)
    unavailable = AgentUnavailabilityTracker(clock=clock)
    config = UnifiedConfig(
        general=GeneralConfig(max_same_agent_retries=2),
        agents={
            "first": AgentConfig(cmd="first"),
            "eligible": AgentConfig(cmd="eligible"),
        },
    )
    registry = AgentRegistry.from_config(config)
    display_context = make_display_context()
    pipeline_deps = dataclasses.replace(
        make_test_pipeline_deps(
            display_context,
            registry_factory=lambda _config: registry,
            recovery_clock=clock,
        ),
        unavailability_store=unavailable,
    )
    invoked: list[str] = []

    def invoke_selected_agent(
        effect: object,
        *_args: object,
        **_kwargs: object,
    ) -> PipelineEvent:
        assert isinstance(effect, InvokeAgentEffect)
        invoked.append(effect.agent_name)
        return PipelineEvent.AGENT_SUCCESS

    def read_selected_artifact(_root: Path) -> str | None:
        if invoked and invoked[-1] == "first":
            unavailable.mark_unavailable(
                "commit",
                "first",
                UnavailabilityReason.NO_OUTPUT_AT_START,
            )
            return None
        return "fix(commit): reselect after missing artifact"

    def delete_artifacts(_root: Path) -> None:
        return None

    def write_prompt(_root: Path, _prompt: str) -> str:
        return str(tmp_path / "commit-prompt.md")

    monkeypatch.setattr(commit_plumbing, "execute_agent_effect", invoke_selected_agent)
    monkeypatch.setattr(commit_cli, "delete_commit_message_artifacts", delete_artifacts)
    monkeypatch.setattr(
        commit_cli,
        "read_commit_message_artifact",
        read_selected_artifact,
    )
    monkeypatch.setattr(
        commit_cli,
        "write_commit_prompt_file",
        write_prompt,
    )

    result = commit_plumbing.run_commit_plumbing(
        diff="diff --git a/file.txt b/file.txt",
        repo_root=tmp_path,
        chain_config=CommitChainConfig(
            registry=registry,
            agents=["first", "eligible"],
            verbose=False,
            agents_policy=AgentsPolicy(),
            general_config=config,
        ),
        display_context=display_context,
        pipeline_deps=pipeline_deps,
    )

    assert result.message == "fix(commit): reselect after missing artifact"
    assert result.outcome is commit_plumbing.CommitAgentOutcome.COMPLETED
    assert invoked == ["first", "eligible"]
