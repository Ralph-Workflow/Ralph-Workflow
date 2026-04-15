"""Tests for ralph/pipeline/runner.py — pipeline runner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.agents.parsers import AgentOutputLine
from ralph.config.enums import JsonParserType
from ralph.config.models import AgentConfig
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
from ralph.pipeline.state import AgentChainState, PipelineState
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

if TYPE_CHECKING:
    from pytest import MonkeyPatch

DEVELOPER_ITERATIONS = 5
REVIEWER_PASSES = 2
SECOND_ITERATION = 2
INTERRUPT_EXIT_CODE = 130


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

        effect = runner_module._determine_effect_from_policy(state, bundle)
        assert isinstance(effect, ExitSuccessEffect)

    def test_failed_phase_returns_exit_failure(self) -> None:
        bundle = _load_default_policy_bundle()
        state = self._make_state(phase="failed")
        state.last_error = "Something went wrong"

        effect = runner_module._determine_effect_from_policy(state, bundle)
        assert isinstance(effect, ExitFailureEffect)
        assert "Something went wrong" in effect.reason

    def test_unknown_phase_returns_exit_failure(self) -> None:
        bundle = _load_default_policy_bundle()
        state = self._make_state(phase="unknown_phase")

        effect = runner_module._determine_effect_from_policy(state, bundle)
        assert isinstance(effect, ExitFailureEffect)
        assert "Unknown phase" in effect.reason

    def test_default_planning_phase_uses_policy_drain_agent(self) -> None:
        bundle = _load_default_policy_bundle()
        state = PipelineState(phase="planning")

        effect = runner_module._determine_effect_from_policy(state, bundle)

        assert isinstance(effect, InvokeAgentEffect)
        assert (
            effect.agent_name
            == bundle.agents.agent_chains[bundle.agents.agent_drains["planning"].chain].agents[0]
        )

    def test_commit_phase_with_requires_commit_uses_commit_effect(self) -> None:
        bundle = _load_default_policy_bundle()
        state = PipelineState(phase="development_commit")

        effect = runner_module._determine_effect_from_policy(state, bundle)
        assert isinstance(effect, CommitEffect)

    def test_review_phase_uses_bound_review_agent(self) -> None:
        bundle = _load_default_policy_bundle()
        state = PipelineState(phase="review")

        effect = runner_module._determine_effect_from_policy(state, bundle)
        assert isinstance(effect, InvokeAgentEffect)
        assert (
            effect.agent_name
            == bundle.agents.agent_chains[bundle.agents.agent_drains["review"].chain].agents[0]
        )

    def test_review_analysis_phase_uses_policy_binding(self) -> None:
        bundle = _load_default_policy_bundle()
        state = PipelineState(phase="review_analysis")

        effect = runner_module._determine_effect_from_policy(state, bundle)
        assert isinstance(effect, InvokeAgentEffect)

    def test_fix_phase_uses_policy_binding(self) -> None:
        bundle = _load_default_policy_bundle()
        state = PipelineState(phase="fix")

        effect = runner_module._determine_effect_from_policy(state, bundle)
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

        effect = runner_module._determine_effect_from_policy(state, broken_bundle)
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

        effect = runner_module._determine_effect_from_policy(state, bundle)

        assert isinstance(effect, InvokeAgentEffect)
        assert effect.agent_name == "claude"


class TestCommitEffect:
    def test_returns_commit_effect(self) -> None:
        effect = runner_module._commit_effect()
        assert isinstance(effect, CommitEffect)
        assert ".agent/tmp/commit_message.json" in effect.message_file


class TestPipelineRunnerLoop:
    def test_save_checkpoint_effect_triggers_checkpoint_and_returns_success(
        self,
        monkeypatch,
    ) -> None:
        state = MagicMock()
        state.phase = "planning"
        effects = [SaveCheckpointEffect(), ExitSuccessEffect()]

        def stub_determine_effect(_state, _bundle):
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
        console_mock.print.assert_called_once_with(
            "[green]Pipeline completed successfully.[/green]"
        )

    def test_exit_failure_effect_returns_failure(self, monkeypatch) -> None:
        state = MagicMock()
        state.phase = "planning"
        console_mock = MagicMock()

        monkeypatch.setattr(
            runner_module,
            "_determine_effect_from_policy",
            lambda _state, _bundle: ExitFailureEffect(reason="bad"),
        )
        monkeypatch.setattr(runner_module, "console", console_mock)
        monkeypatch.setattr(runner_module.ckpt, "save", MagicMock())

        result = runner_module.run(MagicMock(), initial_state=state)

        assert result == 1
        console_mock.print.assert_called_once_with("[red]Pipeline failed:[/red] bad")

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
        console_mock.print.assert_called_once_with("[red]Pipeline failed:[/red] bad error")

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

        def stub_determine_effect(_state, _bundle):
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
        state.copy_with.assert_called_once_with(phase="development", iteration=0)
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

        def stub_determine_effect(_state, _bundle):
            return effects.pop(0)

        execute_effect = MagicMock(return_value=(PipelineEvent.AGENT_SUCCESS, None))
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

        def stub_determine_effect(_state, _bundle):
            return effects.pop(0)

        execute_effect = MagicMock(return_value=(PipelineEvent.AGENT_SUCCESS, None))
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

        def stub_determine_effect(_state, _bundle):
            return effects.pop(0)

        execute_effect = MagicMock(return_value=(PipelineEvent.AGENT_SUCCESS, None))
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
            None,
        )

        assert result[0] == PipelineEvent.AGENT_SUCCESS
        assert result[1] is not None

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
            None,
        )

        assert result[0] == PipelineEvent.AGENT_SUCCESS
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
            None,
        )

        assert result == (PipelineEvent.AGENT_FAILURE, None)

    def test_handles_invocation_error_gracefully(self) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

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
            None,
        )

        assert result[0] == PipelineEvent.AGENT_FAILURE

    def test_handles_unexpected_error_as_failure(self) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

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
            None,
        )

        assert result[0] == PipelineEvent.AGENT_FAILURE

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

        def fake_start_mcp_server(session, workspace, *, bridge_factory=None):
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
            None,
        )

        assert result[0] == PipelineEvent.AGENT_SUCCESS
        assert started["value"] is True
        assert shutdown["value"] is False
        assert seen_options

    def test_reuses_run_scoped_mcp_server_when_provided(self, monkeypatch) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        class FakeBridge:
            def start(self) -> None:
                return

            def shutdown(self) -> None:
                raise AssertionError("run-scoped bridge should not shut down here")

            def agent_endpoint_uri(self) -> str:
                return "http://127.0.0.1:12345/mcp"

            def endpoint_uri(self) -> str:
                return "http://127.0.0.1:12345/mcp"

        bridge = FakeBridge()
        configured_sessions: list[object] = []

        monkeypatch.setattr(
            runner_module,
            "start_mcp_server",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must reuse bridge")),
        )
        monkeypatch.setattr(
            runner_module,
            "configure_mcp_server_session",
            lambda _bridge, session: configured_sessions.append(session),
        )

        result = runner_module._execute_agent_effect(
            effect,
            self._config(),
            runner_module._AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=self.AgentError,
                agent_registry=registry,
            ),
            bridge,
        )

        assert result == (PipelineEvent.AGENT_SUCCESS, bridge)
        assert len(configured_sessions) == 1

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
            None,
        )

        assert result[0] == PipelineEvent.AGENT_SUCCESS
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
            None,
        )

        assert result[0] == PipelineEvent.AGENT_SUCCESS
        printed = "\n".join(
            " ".join(str(arg) for arg in call.args) for call in console_mock.print.call_args_list
        )
        assert "message_start" in printed
        assert "plan complete" in printed
        assert "stop" in printed


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
        )

        assert result == PipelineEvent.COMMIT_SUCCESS
        stage_all.assert_not_called()
        create_commit.assert_not_called()
        assert not message_file.exists()
        assert not text_file.exists()


class TestExecuteEffect:
    def test_save_checkpoint_returns_checkpoint_event(self) -> None:
        result = runner_module._execute_effect(SaveCheckpointEffect(), MagicMock(), None)

        assert result == (PipelineEvent.CHECKPOINT_SAVED, None)

    def test_commit_effect_delegates_to_commit_handler(self, monkeypatch) -> None:
        captured: dict[str, bool] = {}

        def stub_commit(effect, create_commit, stage_all):
            captured["called"] = True
            captured["message_file"] = effect.message_file
            return PipelineEvent.COMMIT_SUCCESS

        monkeypatch.setattr(runner_module, "_execute_commit_effect", stub_commit)
        result = runner_module._execute_effect(CommitEffect(message_file="foo"), MagicMock(), None)

        assert result == (PipelineEvent.COMMIT_SUCCESS, None)
        assert captured.get("called")
        assert captured.get("message_file") == "foo"

    def test_unknown_effect_returns_failure(self) -> None:
        result = runner_module._execute_effect(
            PreparePromptEffect(phase="planning", iteration=0),
            MagicMock(),
            None,
        )

        assert result == (PipelineEvent.AGENT_FAILURE, None)


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
        assert "bash" in rendered
        assert "command=pytest -q" in rendered
        assert "workdir=/tmp/project" in rendered
        assert "{" not in rendered

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
        assert "status=completed" in rendered
        assert "summary=Plan submitted" in rendered
        assert "{" not in rendered
