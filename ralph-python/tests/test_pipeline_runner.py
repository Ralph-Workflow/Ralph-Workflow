"""Tests for ralph/pipeline/runner.py — pipeline runner."""

from __future__ import annotations

import io
import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

import pytest
from rich.console import Console
from rich.text import Text

from ralph.agents.parsers import AgentOutputLine, ClaudeParser
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig
from ralph.mcp.capability_mapping import SessionDrain
from ralph.mcp.tool_names import claude_tool_name_prefix
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import (
    CommitEffect,
    ExitFailureEffect,
    ExitSuccessEffect,
    InvokeAgentEffect,
    PreparePromptEffect,
    SaveCheckpointEffect,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import AgentChainState, CommitState, PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
)
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.prompts.types import SessionCapabilities

DEVELOPER_ITERATIONS = 5
REVIEWER_PASSES = 2
SECOND_ITERATION = 2
INTERRUPT_EXIT_CODE = 130
_TRUNCATED_TEXT_MAX = runner_module._MAX_TEXT_LENGTH + 1  # content + ellipsis
_TRUNCATED_RESULT_BRIEF_MAX = runner_module._MAX_TOOL_RESULT_BRIEF + 1  # content + ellipsis
_TRUNCATED_METADATA_MAX = runner_module._MAX_METADATA_SUMMARY_LENGTH + 1  # content + ellipsis


@lru_cache(maxsize=1)
def _load_default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _registry_factory(return_value):
    class Registry:
        @classmethod
        def from_config(cls, config):
            instance = MagicMock()
            instance.get.return_value = return_value
            return instance

    return Registry


@pytest.fixture(autouse=True)
def _stub_workspace_scope_and_policy(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(
        runner_module, "load_policy_or_die", lambda _path: _load_default_policy_bundle()
    )


class TestCreateInitialState:
    def test_creates_state_with_planning_phase(self) -> None:
        config = MagicMock()
        config.general.developer_iters = DEVELOPER_ITERATIONS
        config.general.reviewer_reviews = REVIEWER_PASSES
        config.agent_chains = {"development": ["claude"], "review": ["claude"]}

        state = runner_module._create_initial_state(config)
        assert state.phase == "planning"
        assert state.total_iterations == DEVELOPER_ITERATIONS
        assert state.total_reviewer_passes == REVIEWER_PASSES
        assert state.dev_chain.agents == ["claude"]
        assert state.rev_chain.agents == ["claude"]

    def test_empty_agent_chains(self) -> None:
        config = MagicMock()
        config.general.developer_iters = 1
        config.general.reviewer_reviews = 1
        config.agent_chains = {}

        state = runner_module._create_initial_state(config)
        assert state.dev_chain.agents == []
        assert state.rev_chain.agents == []

    def test_uses_policy_drain_bindings_when_available(self) -> None:
        config = MagicMock()
        config.general.developer_iters = DEVELOPER_ITERATIONS
        config.general.reviewer_reviews = REVIEWER_PASSES
        config.agent_chains = {}
        config.agent_drains = {}
        agents_policy = AgentsPolicy(
            agent_chains={"planner_chain": AgentChainConfig(agents=["claude"])},
            agent_drains={"planning": AgentDrainConfig(chain="planner_chain")},
        )
        pipeline_policy = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        )

        state = runner_module._create_initial_state(
            config,
            agents_policy=agents_policy,
            pipeline_policy=pipeline_policy,
        )

        assert state.planning_chain.agents == ["claude"]

    def test_creates_state_with_correct_development_budget(self) -> None:
        config = MagicMock()
        config.general.developer_iters = DEVELOPER_ITERATIONS
        config.general.reviewer_reviews = REVIEWER_PASSES
        config.agent_chains = {"development": ["claude"], "review": ["claude"]}
        state = runner_module._create_initial_state(config)
        assert state.development_budget_remaining == DEVELOPER_ITERATIONS

    def test_creates_state_with_correct_review_budget(self) -> None:
        config = MagicMock()
        config.general.developer_iters = DEVELOPER_ITERATIONS
        config.general.reviewer_reviews = REVIEWER_PASSES
        config.agent_chains = {"development": ["claude"], "review": ["claude"]}
        state = runner_module._create_initial_state(config)
        assert state.review_budget_remaining == REVIEWER_PASSES

    def test_creates_state_with_zero_review_budget_when_r_zero(self) -> None:
        config = MagicMock()
        config.general.developer_iters = 1
        config.general.reviewer_reviews = 0
        config.agent_chains = {"development": ["claude"], "review": ["claude"]}
        state = runner_module._create_initial_state(config)
        assert state.review_budget_remaining == 0


class TestDetermineEffect:
    def _make_state(
        self,
        phase: str,
        iteration: int = 0,
        total_iterations: int = 3,
        current_agent: str | None = None,
    ) -> MagicMock:
        state = MagicMock()
        state.phase = phase
        state.iteration = iteration
        state.total_iterations = total_iterations
        state.total_reviewer_passes = 1
        state.current_agent.return_value = current_agent
        return state

    def test_complete_phase_returns_exit_success(self) -> None:
        bundle = _load_default_policy_bundle()
        state = self._make_state(phase="complete")

        effect = runner_module._determine_effect_from_policy(
            state, bundle, WorkspaceScope("/tmp/worktree")
        )
        assert isinstance(effect, ExitSuccessEffect)

    def test_failed_phase_returns_exit_failure(self) -> None:
        bundle = _load_default_policy_bundle()
        state = self._make_state(phase="failed")
        state.last_error = "Something went wrong"

        effect = runner_module._determine_effect_from_policy(
            state, bundle, WorkspaceScope("/tmp/worktree")
        )
        assert isinstance(effect, ExitFailureEffect)
        assert "Something went wrong" in effect.reason

    def test_unknown_phase_returns_exit_failure(self) -> None:
        bundle = _load_default_policy_bundle()
        state = self._make_state(phase="unknown_phase")

        effect = runner_module._determine_effect_from_policy(
            state, bundle, WorkspaceScope("/tmp/worktree")
        )
        assert isinstance(effect, ExitFailureEffect)
        assert "Unknown phase" in effect.reason

    def test_default_planning_phase_uses_policy_drain_agent(self) -> None:
        bundle = _load_default_policy_bundle()
        state = PipelineState(phase="planning")

        effect = runner_module._determine_effect_from_policy(
            state, bundle, WorkspaceScope("/tmp/worktree")
        )

        assert isinstance(effect, InvokeAgentEffect)
        assert (
            effect.agent_name
            == bundle.agents.agent_chains[bundle.agents.agent_drains["planning"].chain].agents[0]
        )

    def test_commit_phase_with_requires_commit_uses_commit_effect(self, tmp_path: Path) -> None:
        bundle = _load_default_policy_bundle()
        state = PipelineState(phase="development_commit", commit=CommitState(agent_invoked=True))

        effect = runner_module._determine_effect_from_policy(
            state, bundle, WorkspaceScope(root=tmp_path, allowed_roots=[tmp_path])
        )
        assert isinstance(effect, CommitEffect)

    def test_review_phase_uses_bound_review_agent(self) -> None:
        bundle = _load_default_policy_bundle()
        state = PipelineState(phase="review")

        effect = runner_module._determine_effect_from_policy(
            state, bundle, WorkspaceScope("/tmp/worktree")
        )
        assert isinstance(effect, InvokeAgentEffect)
        assert (
            effect.agent_name
            == bundle.agents.agent_chains[bundle.agents.agent_drains["review"].chain].agents[0]
        )

    def test_review_analysis_phase_uses_policy_binding(self) -> None:
        bundle = _load_default_policy_bundle()
        state = PipelineState(phase="review_analysis")

        effect = runner_module._determine_effect_from_policy(
            state, bundle, WorkspaceScope("/tmp/worktree")
        )
        assert isinstance(effect, InvokeAgentEffect)

    def test_fix_phase_uses_policy_binding(self) -> None:
        bundle = _load_default_policy_bundle()
        state = PipelineState(phase="fix")

        effect = runner_module._determine_effect_from_policy(
            state, bundle, WorkspaceScope("/tmp/worktree")
        )
        assert isinstance(effect, InvokeAgentEffect)

    def test_missing_bound_agent_returns_exit_failure(self) -> None:
        bundle = _load_default_policy_bundle()
        broken_bundle = bundle.model_copy(
            update={
                "agents": bundle.agents.model_copy(
                    update={
                        "agent_chains": {
                            **bundle.agents.agent_chains,
                            bundle.agents.agent_drains["review"].chain: MagicMock(agents=[]),
                        }
                    }
                )
            }
        )
        state = PipelineState(phase="review")

        effect = runner_module._determine_effect_from_policy(
            state, broken_bundle, WorkspaceScope("/tmp/worktree")
        )
        assert isinstance(effect, ExitFailureEffect)

    def test_policy_driven_custom_phase_uses_policy_drain_agent(self) -> None:
        state = PipelineState(phase="custom_phase")
        bundle = PolicyBundle(
            agents=AgentsPolicy(
                agent_chains={"custom_chain": AgentChainConfig(agents=["claude"])},
                agent_drains={"development": AgentDrainConfig(chain="custom_chain")},
            ),
            pipeline=PipelinePolicy(
                phases={
                    "custom_phase": PhaseDefinition(
                        drain="development",
                        transitions=PhaseTransition(on_success="complete"),
                    ),
                    "complete": PhaseDefinition(
                        drain="complete",
                        transitions=PhaseTransition(
                            on_success="complete",
                            on_loopback="complete",
                        ),
                    ),
                },
                entry_phase="custom_phase",
                terminal_phase="complete",
            ),
            artifacts=ArtifactsPolicy(artifacts={}),
        )

        effect = runner_module._determine_effect_from_policy(
            state, bundle, WorkspaceScope("/tmp/worktree")
        )

        assert isinstance(effect, InvokeAgentEffect)
        assert effect.agent_name == "claude"
        assert effect.drain == "development"

    def test_handle_inline_prepare_prompt_updates_current_drain_from_policy(self) -> None:
        bundle = _load_default_policy_bundle()
        state = PipelineState(phase="planning", current_drain="planning")

        updated = runner_module._handle_inline_effect(
            effect=PreparePromptEffect(phase="development", iteration=0, drain="development"),
            state=state,
            pipeline_policy=bundle.pipeline,
            workspace_scope=WorkspaceScope("/tmp/worktree"),
        )

        assert isinstance(updated, PipelineState)
        assert updated.phase == "development"
        assert updated.current_drain == "development"


class TestCommitEffect:
    def test_returns_commit_effect(self, tmp_path: Path) -> None:
        effect = runner_module._commit_effect(tmp_path)
        assert isinstance(effect, CommitEffect)
        assert ".agent/tmp/commit_message.json" in effect.message_file


def test_materialize_agent_prompt_if_needed_prefixes_claude_tools(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_materialize_prompt_for_phase(**kwargs):
        captured.update(kwargs)
        return "PROMPT.md"

    monkeypatch.setattr(
        runner_module, "materialize_prompt_for_phase", fake_materialize_prompt_for_phase
    )

    class Registry:
        def get(self, name: str) -> AgentConfig | None:
            del name
            return AgentConfig(
                cmd="claude -p",
                output_flag="--output-format=stream-json",
                transport=AgentTransport.CLAUDE,
            )

    runner_module._materialize_agent_prompt_if_needed(
        InvokeAgentEffect(agent_name="planner", phase="planning", prompt_file="planning.txt"),
        MagicMock(),
        _load_default_policy_bundle().pipeline,
        Registry(),
        WorkspaceScope("/tmp/worktree"),
    )

    session_caps = cast("SessionCapabilities", captured["session_caps"])
    assert session_caps.tool_name_prefix == claude_tool_name_prefix()


def test_execute_agent_effect_uses_single_workspace_root(monkeypatch, tmp_path: Path) -> None:
    config = MagicMock()
    config.general.verbosity = 0
    effect = InvokeAgentEffect(agent_name="planner", phase="planning", prompt_file="planning.md")
    agent_config = AgentConfig(cmd="codex", output_flag="--json-stream")

    class RegistryInstance:
        def get(self, name: str) -> AgentConfig | None:
            del name
            return agent_config

    class Registry:
        @classmethod
        def from_config(cls, config):
            del cls, config
            return RegistryInstance()

    seen: dict[str, object] = {}

    def fake_start_mcp_server(session, workspace):
        seen["workspace_root"] = workspace.root

        class Bridge:
            def agent_endpoint_uri(self) -> str:
                return "http://127.0.0.1:9999/mcp"

        return Bridge()

    def fake_shutdown_mcp_server(_bridge) -> None:
        return None

    def fake_materialize_system_prompt(*, workspace_root: Path, name: str) -> str:
        seen["system_prompt_root"] = workspace_root
        seen["system_prompt_name"] = name
        return str(tmp_path / "SYSTEM_PROMPT.md")

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options=None,
    ):
        del config
        seen["prompt_file"] = prompt_file
        seen["workspace_path"] = options.workspace_path if options is not None else None
        return iter(())

    monkeypatch.setattr(runner_module, "start_mcp_server", fake_start_mcp_server)
    monkeypatch.setattr(runner_module, "shutdown_mcp_server", fake_shutdown_mcp_server)
    monkeypatch.setattr(runner_module, "materialize_system_prompt", fake_materialize_system_prompt)
    monkeypatch.setattr(
        runner_module, "resolve_workspace_scope", lambda: runner_module.WorkspaceScope(tmp_path)
    )

    deps = runner_module._AgentExecutionDeps(
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=RuntimeError,
        agent_registry=Registry,
    )

    result = runner_module._execute_agent_effect(effect, config, deps, WorkspaceScope(tmp_path))

    assert result == PipelineEvent.AGENT_SUCCESS
    assert seen["workspace_root"] == tmp_path
    assert seen["system_prompt_root"] == tmp_path
    assert seen["workspace_path"] == tmp_path


class TestPipelineRunnerLoop:
    def test_save_checkpoint_effect_triggers_checkpoint_and_returns_success(
        self,
        monkeypatch,
    ) -> None:
        state = MagicMock()
        state.phase = "planning"
        effects = [SaveCheckpointEffect(), ExitSuccessEffect()]

        def stub_determine_effect(_state, _bundle, _workspace_scope):
            return effects.pop(0)

        ckpt_save = MagicMock()
        reducer_events: list[object] = []

        def stub_reducer(current_state, event):
            reducer_events.append(event)
            return current_state, None

        def stub_reducer_with_policy(current_state, event, _policy=None):
            return stub_reducer(current_state, event)

        console_mock = MagicMock()
        monkeypatch.setattr(runner_module, "_determine_effect_from_policy", stub_determine_effect)
        monkeypatch.setattr(runner_module, "reducer_reduce", stub_reducer_with_policy)
        monkeypatch.setattr(runner_module.ckpt, "save", ckpt_save)
        monkeypatch.setattr(runner_module, "console", console_mock)

        result = runner_module.run(MagicMock(), initial_state=state)

        assert result == 0
        ckpt_save.assert_called_once_with(state)
        assert reducer_events == [PipelineEvent.CHECKPOINT_SAVED]
        # Verify success message was printed (among other display calls)
        printed_args = [
            str(call.args[0]) if call.args else "" for call in console_mock.print.call_args_list
        ]
        assert any("Pipeline completed successfully" in arg for arg in printed_args)

    def test_exit_failure_effect_returns_failure(self, monkeypatch) -> None:
        state = MagicMock()
        state.phase = "planning"
        console_mock = MagicMock()

        monkeypatch.setattr(
            runner_module,
            "_determine_effect_from_policy",
            lambda _state, _bundle, _workspace_scope: ExitFailureEffect(reason="bad"),
        )
        monkeypatch.setattr(runner_module, "console", console_mock)
        monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())

        result = runner_module.run(MagicMock(), initial_state=state)

        assert result == 1
        # Find the Text object with the failure message among all print calls
        rendered_texts = [
            call.args[0]
            for call in console_mock.print.call_args_list
            if call.args and isinstance(call.args[0], Text)
        ]
        assert any(r.plain == "Pipeline failed: bad" for r in rendered_texts)

    def test_keyboard_interrupt_triggers_checkpoint_and_returns_130(self, monkeypatch) -> None:
        state = MagicMock()
        state.phase = "planning"
        interrupted_state = MagicMock()
        state.copy_with.return_value = interrupted_state

        def raise_interrupt(*_args, **_kwargs):
            raise KeyboardInterrupt

        ckpt_save = MagicMock()
        monkeypatch.setattr(runner_module, "_determine_effect_from_policy", raise_interrupt)
        monkeypatch.setattr(runner_module.ckpt, "save", ckpt_save)

        result = runner_module.run(MagicMock(), initial_state=state)

        assert result == INTERRUPT_EXIT_CODE
        state.copy_with.assert_called_once_with(interrupted_by_user=True)
        ckpt_save.assert_called_once_with(interrupted_state)

    def test_final_failed_state_prints_error_without_loop(self, monkeypatch) -> None:
        state = MagicMock()
        state.phase = "failed"
        state.last_error = "bad error"
        console_mock = MagicMock()

        monkeypatch.setattr(
            runner_module,
            "_determine_effect_from_policy",
            lambda *_: (_ for _ in ()).throw(AssertionError("should not run")),
        )
        monkeypatch.setattr(runner_module, "console", console_mock)
        monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())

        result = runner_module.run(MagicMock(), initial_state=state)

        assert result == 1
        rendered_texts = [
            call.args[0]
            for call in console_mock.print.call_args_list
            if call.args and isinstance(call.args[0], Text)
        ]
        assert any(r.plain == "Pipeline failed: bad error" for r in rendered_texts)

    def test_prepare_prompt_effect_advances_state_without_execute_effect(
        self,
        monkeypatch,
    ) -> None:
        state = MagicMock()
        state.phase = "planning"
        advanced_state = MagicMock()
        advanced_state.phase = "development"
        state.copy_with.return_value = advanced_state

        effects = [
            PreparePromptEffect(phase="development", iteration=0),
            ExitSuccessEffect(),
        ]

        def stub_determine_effect(_state, _bundle, _workspace_scope):
            return effects.pop(0)

        execute_effect = MagicMock(return_value=PipelineEvent.AGENT_FAILURE)
        reducer = MagicMock()
        ckpt_save = MagicMock()
        console_mock = MagicMock()

        monkeypatch.setattr(runner_module, "_determine_effect_from_policy", stub_determine_effect)
        monkeypatch.setattr(runner_module, "_execute_effect", execute_effect)
        monkeypatch.setattr(runner_module, "reducer_reduce", reducer)
        monkeypatch.setattr(runner_module.ckpt, "save", ckpt_save)
        monkeypatch.setattr(runner_module, "console", console_mock)

        result = runner_module.run(MagicMock(), initial_state=state)

        assert result == 0
        state.copy_with.assert_called_once_with(
            phase="development",
            iteration=0,
            current_drain="development",
        )
        execute_effect.assert_not_called()
        reducer.assert_not_called()
        ckpt_save.assert_called_once_with(advanced_state)

    def test_invoke_agent_effect_materializes_prompt_before_execution(self, monkeypatch) -> None:
        state = MagicMock()
        state.phase = "planning"

        effects = [
            InvokeAgentEffect(
                agent_name="planner",
                phase="planning",
                prompt_file=".agent/tmp/planning_prompt.md",
            ),
            ExitSuccessEffect(),
        ]

        def stub_determine_effect(_state, _bundle, _workspace_scope):
            return effects.pop(0)

        execute_effect = MagicMock(return_value=PipelineEvent.AGENT_SUCCESS)
        ckpt_save = MagicMock()
        console_mock = MagicMock()
        materialize = MagicMock(return_value=".agent/tmp/planning_prompt.md")
        handle_phase = MagicMock(return_value=[PipelineEvent.AGENT_SUCCESS])

        monkeypatch.setattr(runner_module, "_determine_effect_from_policy", stub_determine_effect)
        monkeypatch.setattr(runner_module, "_execute_effect", execute_effect)
        monkeypatch.setattr(runner_module, "materialize_prompt_for_phase", materialize)
        monkeypatch.setattr(runner_module, "handle_phase", handle_phase)
        monkeypatch.setattr(runner_module, "reducer_reduce", MagicMock(return_value=(state, None)))
        monkeypatch.setattr(runner_module.ckpt, "save", ckpt_save)
        monkeypatch.setattr(runner_module, "console", console_mock)

        result = runner_module.run(MagicMock(), initial_state=state)

        assert result == 0
        materialize.assert_called_once()
        execute_effect.assert_called_once()

    def test_run_passes_policy_to_reducer(self, monkeypatch, tmp_path: Path) -> None:
        state = MagicMock()
        state.phase = "planning"

        effects = [
            InvokeAgentEffect(
                agent_name="planner",
                phase="planning",
                prompt_file=".agent/tmp/planning_prompt.md",
            ),
            ExitSuccessEffect(),
        ]

        def stub_determine_effect(_state, _bundle, _workspace_scope):
            return effects.pop(0)

        execute_effect = MagicMock(return_value=PipelineEvent.AGENT_SUCCESS)
        reducer = MagicMock(return_value=(state, []))
        materialize = MagicMock(return_value=".agent/tmp/planning_prompt.md")
        handle_phase = MagicMock(return_value=[PipelineEvent.AGENT_SUCCESS])
        ckpt_save = MagicMock()
        console_mock = MagicMock()
        policy_bundle = load_policy(tmp_path / ".agent")

        monkeypatch.setattr(runner_module, "_determine_effect_from_policy", stub_determine_effect)
        monkeypatch.setattr(runner_module, "_execute_effect", execute_effect)
        monkeypatch.setattr(runner_module, "materialize_prompt_for_phase", materialize)
        monkeypatch.setattr(runner_module, "handle_phase", handle_phase)
        monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _path: policy_bundle)
        monkeypatch.setattr(runner_module, "reducer_reduce", reducer)
        monkeypatch.setattr(runner_module.ckpt, "save", ckpt_save)
        monkeypatch.setattr(runner_module, "console", console_mock)

        result = runner_module.run(MagicMock(), initial_state=state)

        assert result == 0
        reducer.assert_called_once_with(state, PipelineEvent.AGENT_SUCCESS, policy_bundle.pipeline)

    def test_run_uses_phase_handler_event_after_agent_execution(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        planning_state = PipelineState(
            phase="planning",
            planning_chain=AgentChainState(agents=["claude"], current_index=0, retries=3),
        )
        failed_state = planning_state.copy_with(
            phase="failed",
            previous_phase="planning",
            last_error="Agent chain exhausted in planning",
        )

        effects = [
            InvokeAgentEffect(
                agent_name="planner",
                phase="planning",
                prompt_file=".agent/tmp/planning_prompt.md",
            ),
            ExitFailureEffect(reason="Agent chain exhausted in planning"),
        ]

        def stub_determine_effect(_state, _bundle, _workspace_scope):
            return effects.pop(0)

        execute_effect = MagicMock(return_value=PipelineEvent.AGENT_SUCCESS)
        reducer = MagicMock(return_value=(failed_state, []))
        materialize = MagicMock(return_value=".agent/tmp/planning_prompt.md")
        handle_phase = MagicMock(return_value=[PipelineEvent.AGENT_FAILURE])
        ckpt_save = MagicMock()
        console_mock = MagicMock()
        policy_bundle = load_policy(tmp_path / ".agent")

        monkeypatch.setattr(runner_module, "_determine_effect_from_policy", stub_determine_effect)
        monkeypatch.setattr(runner_module, "_execute_effect", execute_effect)
        monkeypatch.setattr(runner_module, "materialize_prompt_for_phase", materialize)
        monkeypatch.setattr(runner_module, "handle_phase", handle_phase)
        monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _path: policy_bundle)
        monkeypatch.setattr(runner_module, "reducer_reduce", reducer)
        monkeypatch.setattr(runner_module.ckpt, "save", ckpt_save)
        monkeypatch.setattr(runner_module, "console", console_mock)

        result = runner_module.run(MagicMock(), initial_state=planning_state)

        assert result == 1
        reducer.assert_called_once_with(
            planning_state,
            PipelineEvent.AGENT_FAILURE,
            policy_bundle.pipeline,
        )


class TestExecuteAgentEffect:
    class AgentError(Exception):
        pass

    class _FakeBridge:
        def shutdown(self) -> None:
            return

        def agent_endpoint_uri(self) -> str:
            return "http://127.0.0.1:12345/mcp"

    @staticmethod
    def _config(verbosity: int = 2) -> MagicMock:
        config = MagicMock()
        config.general.verbosity = verbosity
        return config

    def test_returns_success_when_invocation_succeeds(self, monkeypatch) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        class FakeBridge:
            def shutdown(self) -> None:
                return

            def agent_endpoint_uri(self) -> str:
                return "http://127.0.0.1:12345/mcp"

        monkeypatch.setattr(
            runner_module, "start_mcp_server", lambda *_args, **_kwargs: FakeBridge()
        )

        result = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=self.AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
        )

        assert result == PipelineEvent.AGENT_SUCCESS

    def test_development_session_gets_expected_mcp_capabilities(self, monkeypatch) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())
        captured: dict[str, object] = {}

        class FakeBridge:
            def shutdown(self) -> None:
                return

            def agent_endpoint_uri(self) -> str:
                return "http://127.0.0.1:12345/mcp"

        def fake_start_mcp_server(session, *_args, **_kwargs):
            captured["capabilities"] = session.capabilities
            return FakeBridge()

        monkeypatch.setattr(runner_module, "start_mcp_server", fake_start_mcp_server)

        result = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=self.AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert captured["capabilities"] == {
            "workspace.read",
            "git.status_read",
            "git.diff_read",
            "artifact.submit",
            "workspace.write_ephemeral",
            "workspace.write_tracked",
            "process.exec_bounded",
            "run.report_progress",
            "env.read",
        }

    def test_custom_phase_uses_bound_drain_capabilities(self, monkeypatch) -> None:
        effect = InvokeAgentEffect(
            agent_name="dev",
            phase="custom_phase",
            prompt_file="PROMPT.md",
            drain="development",
        )
        registry = _registry_factory(MagicMock())
        captured: dict[str, object] = {}

        class FakeBridge:
            def shutdown(self) -> None:
                return

            def agent_endpoint_uri(self) -> str:
                return "http://127.0.0.1:12345/mcp"

        def fake_start_mcp_server(session, *_args, **_kwargs):
            captured["drain"] = session.drain
            captured["capabilities"] = session.capabilities
            return FakeBridge()

        monkeypatch.setattr(runner_module, "start_mcp_server", fake_start_mcp_server)

        result = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=self.AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert captured["drain"] == "development"
        assert captured["capabilities"] == {
            "workspace.read",
            "git.status_read",
            "git.diff_read",
            "artifact.submit",
            "workspace.write_ephemeral",
            "workspace.write_tracked",
            "process.exec_bounded",
            "run.report_progress",
            "env.read",
        }

    def test_returns_failure_when_agent_missing(self) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(None)

        result = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=self.AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
        )

        assert result == PipelineEvent.AGENT_FAILURE

    def test_handles_invocation_error_gracefully(self, monkeypatch: MonkeyPatch) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        monkeypatch.setattr(
            runner_module,
            "start_mcp_server",
            lambda *_args, **_kwargs: self._FakeBridge(),
        )
        monkeypatch.setattr(runner_module, "shutdown_mcp_server", lambda _bridge: None)
        monkeypatch.setattr(
            runner_module, "materialize_system_prompt", lambda **_kwargs: "PROMPT.md"
        )

        def raising_invoke(*_args, **_kwargs):
            raise self.AgentError("boom")

        result = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=raising_invoke,
                agent_invocation_error=self.AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
        )

        assert result == PipelineEvent.AGENT_FAILURE

    def test_handles_unexpected_error_as_failure(self, monkeypatch: MonkeyPatch) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        monkeypatch.setattr(
            runner_module,
            "start_mcp_server",
            lambda *_args, **_kwargs: self._FakeBridge(),
        )
        monkeypatch.setattr(runner_module, "shutdown_mcp_server", lambda _bridge: None)
        monkeypatch.setattr(
            runner_module, "materialize_system_prompt", lambda **_kwargs: "PROMPT.md"
        )

        def raising_value_error(*_args, **_kwargs):
            raise ValueError("boom")

        result = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=raising_value_error,
                agent_invocation_error=self.AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
        )

        assert result == PipelineEvent.AGENT_FAILURE

    def test_starts_and_shuts_down_mcp_bridge_around_invocation(self, monkeypatch) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        started: dict[str, bool] = {"value": False}
        shutdown: dict[str, bool] = {"value": False}

        class FakeBridge:
            def start(self) -> None:
                started["value"] = True

            def shutdown(self) -> None:
                shutdown["value"] = True

            def agent_endpoint_uri(self) -> str:
                return "tcp://127.0.0.1:12345"

            def endpoint_uri(self) -> str:
                return "tcp://127.0.0.1:12345"

        def fake_start_mcp_server(session, workspace):
            bridge = FakeBridge()
            bridge.start()
            return bridge

        monkeypatch.setattr(
            runner_module,
            "start_mcp_server",
            fake_start_mcp_server,
        )

        seen_options: list[object] = []

        def record_invoke(*_args, **kwargs):
            seen_options.append(kwargs.get("options"))
            return iter(["line"])

        result = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=record_invoke,
                agent_invocation_error=self.AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert started["value"] is True
        assert shutdown["value"] is True
        assert seen_options

    def test_starts_fresh_mcp_server_for_each_invocation(self, monkeypatch) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        created: list[int] = []

        class FakeBridge:
            def __init__(self, marker: int) -> None:
                self.marker = marker

            def shutdown(self) -> None:
                return

            def agent_endpoint_uri(self) -> str:
                return f"http://127.0.0.1:{12345 + self.marker}/mcp"

        def fake_start_mcp_server(*_args, **_kwargs):
            marker = len(created)
            created.append(marker)
            return FakeBridge(marker)

        monkeypatch.setattr(runner_module, "start_mcp_server", fake_start_mcp_server)

        first = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=self.AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
        )
        second = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=self.AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
        )

        assert first == PipelineEvent.AGENT_SUCCESS
        assert second == PipelineEvent.AGENT_SUCCESS
        assert created == [0, 1]

    def test_streams_parsed_agent_activity_to_console_by_default(self, monkeypatch) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        agent_config = AgentConfig(
            cmd="codex",
            output_flag="--json-stream",
            json_parser=JsonParserType.CODEX,
        )
        registry = _registry_factory(agent_config)

        class FakeBridge:
            def start(self) -> None:
                return

            def shutdown(self) -> None:
                return

            def agent_endpoint_uri(self) -> str:
                return "tcp://127.0.0.1:12345"

            def endpoint_uri(self) -> str:
                return "tcp://127.0.0.1:12345"

        monkeypatch.setattr(
            runner_module,
            "start_mcp_server",
            lambda *_args, **_kwargs: FakeBridge(),
        )

        console_mock = MagicMock()
        monkeypatch.setattr(runner_module, "console", console_mock)

        result = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(
                    [
                        '{"type":"text_delta","delta":"thinking"}',
                        '{"type":"tool_use","name":"bash","input":{"command":"ls"}}',
                    ]
                ),
                agent_invocation_error=self.AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        printed = "\n".join(
            " ".join(str(arg) for arg in call.args) for call in console_mock.print.call_args_list
        )
        assert "thinking" in printed
        assert "bash" in printed

    def test_streams_non_text_parsed_events_too(self, monkeypatch) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        agent_config = AgentConfig(
            cmd="codex",
            output_flag="--json-stream",
            json_parser=JsonParserType.CODEX,
        )
        registry = _registry_factory(agent_config)

        class FakeBridge:
            def start(self) -> None:
                return

            def shutdown(self) -> None:
                return

            def agent_endpoint_uri(self) -> str:
                return "tcp://127.0.0.1:12345"

            def endpoint_uri(self) -> str:
                return "tcp://127.0.0.1:12345"

        monkeypatch.setattr(
            runner_module,
            "start_mcp_server",
            lambda *_args, **_kwargs: FakeBridge(),
        )

        console_mock = MagicMock()
        monkeypatch.setattr(runner_module, "console", console_mock)

        result = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(
                    [
                        '{"type":"thread.started"}',
                        '{"type":"result","result":"plan complete"}',
                        '{"type":"turn.completed"}',
                    ]
                ),
                agent_invocation_error=self.AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        printed = "\n".join(
            " ".join(str(arg) for arg in call.args) for call in console_mock.print.call_args_list
        )
        assert "message_start" in printed
        assert "plan complete" in printed
        assert "stop" in printed


def test_determine_effect_invokes_commit_agent_when_agent_not_yet_invoked(
    tmp_path: Path,
) -> None:
    policy_bundle = MagicMock()
    phase_def = MagicMock()
    phase_def.requires_commit = True
    phase_def.drain = "development_commit"
    policy_bundle.pipeline.phases.get.return_value = phase_def
    policy_bundle.agents.agent_drains.get.return_value = MagicMock(chain="commit_chain")
    policy_bundle.agents.agent_chains.get.return_value = MagicMock(agents=["commit-agent"])

    state = PipelineState(phase="development_commit", commit=CommitState(agent_invoked=False))
    workspace_scope = WorkspaceScope(root=tmp_path, allowed_roots=[tmp_path])

    effect = runner_module._determine_effect_from_policy(state, policy_bundle, workspace_scope)

    assert isinstance(effect, InvokeAgentEffect)
    assert effect.agent_name == "commit-agent"
    assert effect.phase == "development_commit"


def test_determine_effect_commits_after_agent_invoked(
    tmp_path: Path,
) -> None:
    policy_bundle = MagicMock()
    phase_def = MagicMock()
    phase_def.requires_commit = True
    policy_bundle.pipeline.phases.get.return_value = phase_def

    state = PipelineState(phase="development_commit", commit=CommitState(agent_invoked=True))
    workspace_scope = WorkspaceScope(root=tmp_path, allowed_roots=[tmp_path])

    effect = runner_module._determine_effect_from_policy(state, policy_bundle, workspace_scope)

    assert isinstance(effect, CommitEffect)
    assert str(tmp_path) in effect.message_file
    assert "commit_message.json" in effect.message_file


class TestExecuteCommitEffect:
    def test_returns_success_when_commit_succeeds(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        stage_all = MagicMock()
        create_commit = MagicMock(return_value="sha")
        message_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
        text_file = tmp_path / ".agent" / "tmp" / "commit-message.txt"
        message_file.parent.mkdir(parents=True, exist_ok=True)
        text_file.write_text("fix: pipeline artifact message", encoding="utf-8")
        monkeypatch.setattr(runner_module, "_repo_has_commit_work", lambda _repo_root: True)
        message_file.write_text(
            json.dumps(
                {
                    "name": "commit_message",
                    "type": "commit_message",
                    "content": {"type": "commit", "subject": "fix: pipeline artifact message"},
                    "created_at": "STATIC",
                    "updated_at": "STATIC",
                    "metadata": {},
                }
            ),
            encoding="utf-8",
        )

        result = runner_module._execute_commit_effect(
            CommitEffect(message_file=str(message_file)),
            create_commit,
            stage_all,
            tmp_path,
        )

        assert result == PipelineEvent.COMMIT_SUCCESS
        stage_all.assert_called_once_with(str(tmp_path))
        create_commit.assert_called_once_with(str(tmp_path), "fix: pipeline artifact message")
        assert not message_file.exists()
        assert not text_file.exists()

    def test_returns_failure_when_create_commit_raises(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        stage_all = MagicMock()
        message_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
        text_file = tmp_path / ".agent" / "tmp" / "commit-message.txt"
        message_file.parent.mkdir(parents=True, exist_ok=True)
        text_file.write_text("fix: pipeline artifact message", encoding="utf-8")
        monkeypatch.setattr(runner_module, "_repo_has_commit_work", lambda _repo_root: True)
        message_file.write_text(
            json.dumps(
                {
                    "name": "commit_message",
                    "type": "commit_message",
                    "content": {"type": "commit", "subject": "fix: pipeline artifact message"},
                    "created_at": "STATIC",
                    "updated_at": "STATIC",
                    "metadata": {},
                }
            ),
            encoding="utf-8",
        )

        def fail_create(*_):
            raise RuntimeError("boom")

        result = runner_module._execute_commit_effect(
            CommitEffect(message_file=str(message_file)),
            fail_create,
            stage_all,
            tmp_path,
        )

        assert result == PipelineEvent.COMMIT_FAILURE
        assert message_file.exists()
        assert text_file.exists()

    def test_returns_failure_when_message_file_missing(self, tmp_path: Path) -> None:
        stage_all = MagicMock()
        create_commit = MagicMock()

        result = runner_module._execute_commit_effect(
            CommitEffect(message_file=str(tmp_path / "missing.txt")),
            create_commit,
            stage_all,
            tmp_path,
        )

        assert result == PipelineEvent.COMMIT_FAILURE
        stage_all.assert_not_called()
        create_commit.assert_not_called()

    def test_skips_commit_when_worktree_has_no_changes(
        self, tmp_path: Path, monkeypatch: MonkeyPatch
    ) -> None:
        stage_all = MagicMock()
        create_commit = MagicMock()
        message_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
        text_file = tmp_path / ".agent" / "tmp" / "commit-message.txt"
        message_file.parent.mkdir(parents=True, exist_ok=True)
        text_file.write_text("fix: skip empty worktree", encoding="utf-8")
        message_file.write_text(
            json.dumps(
                {
                    "name": "commit_message",
                    "type": "commit_message",
                    "content": {"type": "commit", "subject": "fix: skip empty worktree"},
                    "created_at": "STATIC",
                    "updated_at": "STATIC",
                    "metadata": {},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(runner_module, "_repo_has_commit_work", lambda _repo_root: False)

        result = runner_module._execute_commit_effect(
            CommitEffect(message_file=str(message_file)),
            create_commit,
            stage_all,
            tmp_path,
        )

        assert result == PipelineEvent.COMMIT_SUCCESS
        stage_all.assert_not_called()
        create_commit.assert_not_called()
        assert not message_file.exists()
        assert not text_file.exists()


class TestExecuteEffect:
    def test_save_checkpoint_returns_checkpoint_event(self) -> None:
        result = runner_module._execute_effect(
            SaveCheckpointEffect(), MagicMock(), WorkspaceScope("/tmp/worktree")
        )

        assert result == PipelineEvent.CHECKPOINT_SAVED

    def test_commit_effect_delegates_to_commit_handler(self, monkeypatch) -> None:
        captured: dict[str, bool] = {}

        def stub_commit(effect, create_commit, stage_all, repo_root):
            captured["called"] = True
            captured["message_file"] = effect.message_file
            return PipelineEvent.COMMIT_SUCCESS

        monkeypatch.setattr(runner_module, "_execute_commit_effect", stub_commit)
        result = runner_module._execute_effect(
            CommitEffect(message_file="foo"), MagicMock(), WorkspaceScope("/tmp/worktree")
        )

        assert result == PipelineEvent.COMMIT_SUCCESS
        assert captured.get("called")
        assert captured.get("message_file") == "foo"

    def test_unknown_effect_returns_failure(self) -> None:
        result = runner_module._execute_effect(
            PreparePromptEffect(phase="planning", iteration=0),
            MagicMock(),
            WorkspaceScope("/tmp/worktree"),
        )

        assert result == PipelineEvent.AGENT_FAILURE


class TestRenderAgentActivityLine:
    def test_tool_use_includes_human_readable_input_summary(self) -> None:
        output = AgentOutputLine(
            type="tool_use",
            content="bash",
            metadata={
                "tool": "bash",
                "input": {
                    "command": "pytest -q",
                    "workdir": "/tmp/project",
                },
            },
        )

        rendered = runner_module._render_agent_activity_line(output, "dev")

        assert rendered is not None
        assert isinstance(rendered, Text)
        assert "bash" in rendered.plain
        assert "command=pytest -q" in rendered.plain
        assert "workdir=/tmp/project" in rendered.plain
        assert "{" not in rendered.plain

    def test_non_text_event_summary_avoids_raw_json_dump(self) -> None:
        output = AgentOutputLine(
            type="item_plan_result",
            metadata={
                "status": "completed",
                "summary": "Plan submitted",
                "result": {"steps": 3},
            },
        )

        rendered = runner_module._render_agent_activity_line(output, "dev")

        assert rendered is not None
        assert isinstance(rendered, Text)
        assert "status=completed" in rendered.plain
        assert "summary=Plan submitted" in rendered.plain
        assert "{" not in rendered.plain

    def test_tool_result_renders_content(self) -> None:
        output = AgentOutputLine(
            type="tool_result",
            content="{'matches': 3, 'path': 'src'}",
            metadata={
                "tool": "grep",
                "input": {"pattern": "TODO", "path": "src"},
                "result": {"matches": 3, "path": "src"},
            },
        )

        rendered = runner_module._render_agent_activity_line(output, "dev")

        assert rendered is not None
        assert isinstance(rendered, Text)
        assert "result" in rendered.plain
        assert "{'matches': 3, 'path': 'src'}" in rendered.plain

    def test_claude_assistant_text_renders_without_extra_assistant_summary_line(self) -> None:
        parser = ClaudeParser()
        parsed = list(
            parser.parse(
                iter(
                    [
                        (
                            '{"type":"assistant","message":{"content":['
                            '{"type":"text","text":"Final response"}]}}'
                        )
                    ]
                )
            )
        )

        rendered = []
        for output in parsed:
            rendered_line = runner_module._render_agent_activity_line(output, "dev")
            if rendered_line is not None:
                rendered.append(rendered_line)

        assert [item.plain for item in rendered] == ["dev: Final response"]

    def test_tool_use_output_escapes_markup_like_input_before_console_render(self) -> None:
        output = AgentOutputLine(
            type="tool_use",
            content="Write",
            metadata={
                "input": {
                    "file_path": "/tmp/[unsafe].py",
                    "newText": "[/{color}]",
                }
            },
        )

        rendered = runner_module._render_agent_activity_line(output, "claude")

        assert rendered is not None

        console = Console(file=io.StringIO(), force_terminal=False, color_system=None)
        console.print(rendered)

    def test_analysis_prompt_session_drain_preserves_analysis_identity(self) -> None:
        assert (
            runner_module._prompt_session_drain_for_phase("development_analysis")
            is SessionDrain.DEVELOPMENT_ANALYSIS
        )
        assert (
            runner_module._prompt_session_drain_for_phase("review_analysis")
            is SessionDrain.REVIEW_ANALYSIS
        )

    def test_text_truncation_for_long_content(self) -> None:
        long_content = "a" * 300
        output = AgentOutputLine(type="text", content=long_content)

        rendered = runner_module._render_agent_activity_line(output, "dev")

        assert rendered is not None
        assert "…" in rendered.plain
        content_part = rendered.plain.split(": ", 1)[1]
        assert len(content_part) <= _TRUNCATED_TEXT_MAX

    def test_tool_input_truncation(self) -> None:
        long_value = "x" * 200
        output = AgentOutputLine(
            type="tool_use",
            content="read_file",
            metadata={"input": {"path": long_value}},
        )

        rendered = runner_module._render_agent_activity_line(output, "dev")

        assert rendered is not None
        assert "…" in rendered.plain

    def test_error_format_with_symbol(self) -> None:
        output = AgentOutputLine(type="error", content="something broke")

        rendered = runner_module._render_agent_activity_line(output, "dev")

        assert rendered is not None
        assert "✗" in rendered.plain
        assert "something broke" in rendered.plain

    def test_tool_result_brief_for_very_long_content(self) -> None:
        long_result = "z" * 600
        output = AgentOutputLine(type="tool_result", content=long_result)

        rendered = runner_module._render_agent_activity_line(output, "dev")

        assert rendered is not None
        assert "…" in rendered.plain
        content_part = rendered.plain.split(": ", 1)[1]
        assert len(content_part) <= _TRUNCATED_RESULT_BRIEF_MAX

    def test_metadata_summary_caps_total_length(self) -> None:
        metadata: dict[str, object] = {
            "status": "a" * 50,
            "summary": "b" * 50,
            "phase": "c" * 50,
        }
        result = runner_module._metadata_summary(metadata)
        assert len(result) <= _TRUNCATED_METADATA_MAX
