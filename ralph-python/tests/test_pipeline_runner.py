"""Tests for ralph/pipeline/runner.py — pipeline runner."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import ralph.pipeline.runner as runner_module
from ralph.agents.parsers import AgentOutputLine
from ralph.config.enums import JsonParserType
from ralph.config.models import AgentConfig
from ralph.pipeline.effects import (
    CommitEffect,
    ExitFailureEffect,
    ExitSuccessEffect,
    InvokeAgentEffect,
    PreparePromptEffect,
    SaveCheckpointEffect,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.runner import (
    _agent_or_advance,
    _agent_or_next_phase,
    _commit_effect,
    _create_initial_state,
    _determine_effect,
)

if TYPE_CHECKING:
    from pathlib import Path

DEVELOPER_ITERATIONS = 5
REVIEWER_PASSES = 2
SECOND_ITERATION = 2
INTERRUPT_EXIT_CODE = 130


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

        state = _create_initial_state(config)
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

        state = _create_initial_state(config)
        assert state.dev_chain.agents == []
        assert state.rev_chain.agents == []


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

    def test_planning_phase_with_planner_returns_invoke(self) -> None:
        config = MagicMock()
        config.agent_drains = {"planning": "plan"}
        config.agent_chains = {"plan": ["claude"]}
        state = self._make_state(phase="planning")

        effect = _determine_effect(state, config)
        assert isinstance(effect, InvokeAgentEffect)
        assert effect.agent_name == "claude"

    def test_planning_phase_without_planner_returns_prepare_prompt(self) -> None:
        config = MagicMock()
        config.agent_drains = {}
        config.agent_chains = {}
        state = self._make_state(phase="planning")

        effect = _determine_effect(state, config)
        assert isinstance(effect, PreparePromptEffect)
        assert effect.phase == "development"

    def test_development_with_no_agent_returns_prepare_prompt(self) -> None:
        config = MagicMock()
        state = self._make_state(
            phase="development",
            iteration=0,
            total_iterations=3,
            current_agent=None,
        )

        effect = _determine_effect(state, config)
        assert isinstance(effect, PreparePromptEffect)
        assert effect.phase == "development"

    def test_development_with_iteration_exhausted_returns_review(self) -> None:
        config = MagicMock()
        state = self._make_state(
            phase="development",
            iteration=2,  # Last iteration (total=3)
            total_iterations=3,
            current_agent=None,
        )

        effect = _determine_effect(state, config)
        assert isinstance(effect, PreparePromptEffect)
        assert effect.phase == "review"

    def test_development_with_agent_returns_invoke(self) -> None:
        config = MagicMock()
        state = self._make_state(
            phase="development",
            current_agent="claude",
        )

        effect = _determine_effect(state, config)
        assert isinstance(effect, InvokeAgentEffect)
        assert effect.agent_name == "claude"

    def test_review_with_agent_returns_invoke(self) -> None:
        config = MagicMock()
        state = self._make_state(
            phase="review",
            current_agent="claude",
        )

        effect = _determine_effect(state, config)
        assert isinstance(effect, InvokeAgentEffect)

    def test_development_commit_returns_commit_effect(self) -> None:
        config = MagicMock()
        state = self._make_state(phase="development_commit")

        effect = _determine_effect(state, config)
        assert isinstance(effect, CommitEffect)

    def test_review_commit_returns_commit_effect(self) -> None:
        config = MagicMock()
        state = self._make_state(phase="review_commit")

        effect = _determine_effect(state, config)
        assert isinstance(effect, CommitEffect)

    def test_complete_phase_returns_exit_success(self) -> None:
        config = MagicMock()
        state = self._make_state(phase="complete")

        effect = _determine_effect(state, config)
        assert isinstance(effect, ExitSuccessEffect)

    def test_failed_phase_returns_exit_failure(self) -> None:
        config = MagicMock()
        state = self._make_state(phase="failed")
        state.last_error = "Something went wrong"

        effect = _determine_effect(state, config)
        assert isinstance(effect, ExitFailureEffect)
        assert "Something went wrong" in effect.reason

    def test_unknown_phase_returns_exit_failure(self) -> None:
        config = MagicMock()
        state = self._make_state(phase="unknown_phase")

        effect = _determine_effect(state, config)
        assert isinstance(effect, ExitFailureEffect)
        assert "Unknown phase" in effect.reason


class TestAgentOrAdvance:
    def _make_state(
        self, phase: str, iteration: int, total_iterations: int, current_agent: str | None
    ) -> MagicMock:
        state = MagicMock()
        state.phase = phase
        state.iteration = iteration
        state.total_iterations = total_iterations
        state.current_agent.return_value = current_agent
        return state

    def test_with_agent_returns_invoke_effect(self) -> None:
        state = self._make_state("development", 0, 3, "claude")
        effect = _agent_or_advance(state, "review")
        assert isinstance(effect, InvokeAgentEffect)
        assert effect.agent_name == "claude"

    def test_without_agent_increments_iteration(self) -> None:
        state = self._make_state("development", 1, DEVELOPER_ITERATIONS, None)
        effect = _agent_or_advance(state, "review")
        assert isinstance(effect, PreparePromptEffect)
        assert effect.iteration == SECOND_ITERATION

    def test_without_agent_last_iteration_goes_to_fallback(self) -> None:
        state = self._make_state("development", 4, 5, None)
        effect = _agent_or_advance(state, "review")
        assert isinstance(effect, PreparePromptEffect)
        assert effect.phase == "review"
        assert effect.iteration == 0


class TestAgentOrNextPhase:
    def _make_state(self, phase: str, current_agent: str | None) -> MagicMock:
        state = MagicMock()
        state.phase = phase
        state.current_agent.return_value = current_agent
        return state

    def test_with_agent_returns_invoke_effect(self) -> None:
        state = self._make_state("review", "claude")
        effect = _agent_or_next_phase(state, "development_commit")
        assert isinstance(effect, InvokeAgentEffect)
        assert effect.agent_name == "claude"

    def test_without_agent_returns_fallback_phase(self) -> None:
        state = self._make_state("review", None)
        effect = _agent_or_next_phase(state, "development_commit")
        assert isinstance(effect, PreparePromptEffect)
        assert effect.phase == "development_commit"


class TestCommitEffect:
    def test_returns_commit_effect(self) -> None:
        effect = _commit_effect()
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

        def stub_determine_effect(_state, _config):
            return effects.pop(0)

        ckpt_save = MagicMock()
        reducer_events: list[object] = []

        def stub_reducer(current_state, event):
            reducer_events.append(event)
            return current_state, None

        console_mock = MagicMock()
        monkeypatch.setattr(runner_module, "_determine_effect", stub_determine_effect)
        monkeypatch.setattr(runner_module, "reducer_reduce", stub_reducer)
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
            "_determine_effect",
            lambda _state, _config: ExitFailureEffect(reason="bad"),
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
        monkeypatch.setattr(runner_module, "_determine_effect", raise_interrupt)
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
            "_determine_effect",
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

        def stub_determine_effect(_state, _config):
            return effects.pop(0)

        execute_effect = MagicMock(return_value=PipelineEvent.AGENT_FAILURE)
        reducer = MagicMock()
        ckpt_save = MagicMock()
        console_mock = MagicMock()

        monkeypatch.setattr(runner_module, "_determine_effect", stub_determine_effect)
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
    def test_returns_success_when_commit_succeeds(self, tmp_path: Path) -> None:
        stage_all = MagicMock()
        create_commit = MagicMock(return_value="sha")
        message_file = tmp_path / "commit_message.json"
        message_file.write_text(
            json.dumps(
                {
                    "name": "commit_message",
                    "type": "commit_message",
                    "content": {"message": "fix: pipeline artifact message"},
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
        stage_all.assert_called_once_with(".")
        create_commit.assert_called_once_with(".", "fix: pipeline artifact message")

    def test_returns_failure_when_create_commit_raises(self, tmp_path: Path) -> None:
        stage_all = MagicMock()
        message_file = tmp_path / "commit_message.json"
        message_file.write_text(
            json.dumps(
                {
                    "name": "commit_message",
                    "type": "commit_message",
                    "content": {"message": "fix: pipeline artifact message"},
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
