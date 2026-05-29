"""Tests for ralph/pipeline/runner.py — pipeline runner."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
)
from ralph.config.enums import (
    AgentTransport,
    JsonParserType,
)
from ralph.config.models import AgentConfig, CcsConfig
from ralph.display.context import make_display_context
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import (
    InvokeAgentEffect,
)
from ralph.pipeline.events import PipelineEvent
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.policy.models import (
        PolicyBundle,
    )
from tests.test_pipeline_runner_execute_agent_effect_2_b_helper_agenterror import AgentError

DEVELOPER_ITERATIONS = 5
REVIEWER_PASSES = 2
SECOND_ITERATION = 2
INTERRUPT_EXIT_CODE = 130
_TRUNCATED_TEXT_MAX = runner_module.MAX_TEXT_LENGTH + 1  # content + ellipsis
_TRUNCATED_RESULT_BRIEF_MAX = runner_module.MAX_TOOL_RESULT_BRIEF + 1  # content + ellipsis
_TRUNCATED_METADATA_MAX = runner_module.MAX_METADATA_SUMMARY_LENGTH + 1  # content + ellipsis
_AVAILABLE_WIDTH_FLOOR = 40
_TRUNCATE_RESULT_LEN = 6  # 5 chars + 1 ellipsis char


@lru_cache(maxsize=1)
def _load_default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _registry_factory(return_value: object) -> object:
    class Registry:
        @classmethod
        def from_config(cls, config: object) -> object:
            instance = MagicMock()
            instance.get.return_value = return_value
            return instance

    return Registry


def _install_runner_display_context(
    monkeypatch: MonkeyPatch,
    *,
    width: int = 120,
) -> Console:
    console = Console(record=True, force_terminal=False, width=width, color_system=None)
    ctx = make_display_context(console=console, force_width=width, force_mode="wide")
    monkeypatch.setattr(runner_module, "make_display_context", lambda **_kwargs: ctx)
    return console


def _config_with_agents(
    *,
    agent_chains: dict[str, list[str]],
    agent_drains: dict[str, str],
) -> object:
    config = MagicMock()
    config.agent_chains = agent_chains
    config.agent_drains = agent_drains
    return config


def _write_minimal_plan_artifacts(
    root: Path,
    *,
    context: str = "Existing plan",
) -> None:
    (root / ".agent" / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / ".agent" / "artifacts" / "plan.json").write_text(
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": context,
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    },
                    "skills_mcp": {
                        "skills": [
                            "test-driven-development",
                            "verification-before-completion",
                        ],
                        "mcps": [],
                    },
                    "steps": [{"number": 1, "title": "Revise", "content": "keep context"}],
                    "critical_files": {
                        "primary_files": [{"path": "src/plan.py", "action": "modify"}],
                        "reference_files": [],
                    },
                    "risks_mitigations": [{"risk": "drift", "mitigation": "preserve"}],
                    "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
                    "work_units": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (root / ".agent" / "PLAN.md").write_text(
        f"# Execution Plan\n\n{context}.\n",
        encoding="utf-8",
    )


def _write_minimal_plan_draft(root: Path, *, context: str = "Existing draft") -> None:
    artifact_dir = root / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / ".plan_draft.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "started_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:01+00:00",
                "sections": {
                    "summary": {
                        "context": context,
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _stub_workspace_scope_and_policy(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(
        runner_module, "load_policy_or_die", lambda _path: _load_default_policy_bundle()
    )


class TestExecuteAgentEffectB:
    @staticmethod
    def _config(verbosity: int = 2) -> MagicMock:
        config = MagicMock()
        config.general.verbosity = verbosity
        config.agents = {}
        config.ccs = CcsConfig()
        config.ccs_aliases = {"mm": "ccs mm"}
        return config

    def test_streams_parsed_agent_activity_to_console_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

        captured_console = _install_runner_display_context(monkeypatch)

        result = runner_module.execute_agent_effect(
            effect,
            self._config(),
            runner_module.AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(
                    [
                        '{"type":"text_delta","delta":"thinking"}',
                        '{"type":"tool_use","name":"bash","input":{"command":"ls"}}',
                    ]
                ),
                agent_invocation_error=AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=runner_module.make_display_context(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        printed = captured_console.export_text()
        assert "thinking" in printed
        assert "bash" in printed

    def test_streams_non_text_parsed_events_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
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

        captured_console = _install_runner_display_context(monkeypatch)

        result = runner_module.execute_agent_effect(
            effect,
            self._config(),
            runner_module.AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(
                    [
                        '{"type":"thread.started"}',
                        '{"type":"result","result":"plan complete"}',
                        '{"type":"turn.completed"}',
                    ]
                ),
                agent_invocation_error=AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=runner_module.make_display_context(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        printed = captured_console.export_text()
        # Lifecycle events (thread.started) are suppressed — no noise in output
        assert "message_start" not in printed
        # Meaningful events still stream
        assert "plan complete" in printed
        assert "stop" in printed

    def test_retries_transient_connectivity_failures_with_session_resume(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        prompt_file = tmp_path / "PROMPT.md"
        prompt_file.write_text("ship it", encoding="utf-8")
        effect = InvokeAgentEffect(
            agent_name="dev",
            phase="development",
            prompt_file=str(prompt_file),
        )
        agent_config = AgentConfig(
            cmd="claude -p",
            output_flag="--output-format=stream-json",
            print_flag="--print",
            streaming_flag="--include-partial-messages",
            session_flag="--resume {}",
            json_parser=JsonParserType.CLAUDE,
            transport=AgentTransport.CLAUDE,
        )
        registry = _registry_factory(agent_config)

        class FakeBridge:
            def shutdown(self) -> None:
                return

            def agent_endpoint_uri(self) -> str:
                return "http://127.0.0.1:12345/mcp"

        def fake_start_mcp_server(*_args: object, **_kwargs: object) -> object:
            return FakeBridge()

        monkeypatch.setattr(effect_executor_module, "start_mcp_server", fake_start_mcp_server)

        seen_session_ids: list[str | None] = []

        def fake_invoke_agent(
            config: object, prompt_file: object, *, options: object = None
        ) -> object:
            del config, prompt_file
            seen_session_ids.append(None if options is None else options.session_id)
            if len(seen_session_ids) == 1:

                def _first_attempt() -> object:
                    yield '{"session_id":"claude-session-42"}'
                    raise AgentInvocationError("claude", 1, "connection refused")

                return _first_attempt()
            return iter(
                ['{"type":"assistant","message":{"content":[{"type":"text","text":"done"}]}}']
            )

        result = runner_module.execute_agent_effect(
            effect,
            self._config(),
            runner_module.AgentExecutionDeps(
                invoke_agent=fake_invoke_agent,
                agent_invocation_error=AgentInvocationError,
                agent_registry=registry,
            ),
            WorkspaceScope(tmp_path),
            display_context=make_display_context(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert seen_session_ids == [None, "claude-session-42"]

    def test_retries_inactivity_failures_with_summary_prompt(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        prompt_file = tmp_path / "PROMPT.md"
        prompt_file.write_text("implement the change", encoding="utf-8")
        effect = InvokeAgentEffect(
            agent_name="dev",
            phase="development",
            prompt_file=str(prompt_file),
        )
        agent_config = AgentConfig(
            cmd="codex",
            output_flag="--json-stream",
            json_parser=JsonParserType.CODEX,
        )
        registry = _registry_factory(agent_config)

        class FakeBridge:
            def shutdown(self) -> None:
                return

            def agent_endpoint_uri(self) -> str:
                return "http://127.0.0.1:12345/mcp"

        monkeypatch.setattr(
            runner_module,
            "start_mcp_server",
            lambda *_args, **_kwargs: FakeBridge(),
        )

        seen_prompt_files: list[str] = []

        def fake_invoke_agent(
            config: object, prompt_file: object, *, options: object = None
        ) -> object:
            del config, options
            seen_prompt_files.append(prompt_file)
            if len(seen_prompt_files) == 1:

                def _first_attempt() -> object:
                    yield '{"type":"text","content":"drafted the fix"}'
                    raise AgentInactivityTimeoutError("codex", 30, ["drafted the fix"])

                return _first_attempt()
            return iter(['{"type":"result","result":"finished"}'])

        result = runner_module.execute_agent_effect(
            effect,
            self._config(),
            runner_module.AgentExecutionDeps(
                invoke_agent=fake_invoke_agent,
                agent_invocation_error=AgentInvocationError,
                agent_registry=registry,
            ),
            WorkspaceScope(tmp_path),
            display_context=make_display_context(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert seen_prompt_files[0] == str(prompt_file)
        assert seen_prompt_files[1] != str(prompt_file)
        retry_prompt = Path(seen_prompt_files[1]).read_text(encoding="utf-8")
        assert "inactivity timeout" in retry_prompt
        assert "drafted the fix" in retry_prompt
