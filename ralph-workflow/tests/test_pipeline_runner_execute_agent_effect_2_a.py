"""Tests for ralph/pipeline/runner.py — pipeline runner."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from ralph.config.enums import (
    AgentTransport,
)
from ralph.config.mcp_loader import McpConfigError
from ralph.config.models import AgentConfig, CcsConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import (
    InvokeAgentEffect,
    PreparePromptEffect,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope
from tests.test_pipeline_runner_execute_agent_effect_2_a_agent_error import AgentError
from tests.test_pipeline_runner_execute_agent_effect_2_a_fake_bridge import _FakeBridge

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.policy.models import (
        PolicyBundle,
    )


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


class TestExecuteAgentEffectA:
    @staticmethod
    def _config(verbosity: int = 2) -> MagicMock:
        config = MagicMock()
        config.general.verbosity = verbosity
        config.agents = {}
        config.ccs = CcsConfig()
        config.ccs_aliases = {"mm": "ccs mm"}
        return config

    def test_returns_success_when_invocation_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        class FakeBridge:
            def shutdown(self) -> None:
                return

            def agent_endpoint_uri(self) -> str:
                return "http://127.0.0.1:12345/mcp"

        monkeypatch.setattr(
            effect_executor_module, "start_mcp_server", lambda *_args, **_kwargs: FakeBridge()
        )

        result = runner_module.execute_agent_effect(
            effect,
            self._config(),
            runner_module.AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS

    def test_development_session_gets_expected_mcp_capabilities(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())
        captured: dict[str, object] = {}

        class FakeBridge:
            def shutdown(self) -> None:
                return

            def agent_endpoint_uri(self) -> str:
                return "http://127.0.0.1:12345/mcp"

        def fake_start_mcp_server(session: object, *_args: object, **_kwargs: object) -> object:
            captured["capabilities"] = session.capabilities
            return FakeBridge()

        monkeypatch.setattr(effect_executor_module, "start_mcp_server", fake_start_mcp_server)

        result = runner_module.execute_agent_effect(
            effect,
            self._config(),
            runner_module.AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert captured["capabilities"] == {
            "workspace.read",
            "workspace.metadata_read",
            "git.status_read",
            "git.diff_read",
            "artifact.submit",
            "artifact.plan_read",
            "workspace.write_ephemeral",
            "workspace.write_tracked",
            "workspace.edit",
            "workspace.delete",
            "process.exec_bounded",
            "run.report_progress",
            "env.read",
            "web.search",
            "web.visit",
            "media.read",
        }

    def test_custom_phase_uses_bound_drain_capabilities(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

        def fake_start_mcp_server(session: object, *_args: object, **_kwargs: object) -> object:
            captured["drain"] = session.drain
            captured["capabilities"] = session.capabilities
            return FakeBridge()

        monkeypatch.setattr(effect_executor_module, "start_mcp_server", fake_start_mcp_server)

        result = runner_module.execute_agent_effect(
            effect,
            self._config(),
            runner_module.AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert captured["drain"] == "development"
        assert captured["capabilities"] == {
            "workspace.read",
            "workspace.metadata_read",
            "git.status_read",
            "git.diff_read",
            "artifact.submit",
            "artifact.plan_read",
            "workspace.write_ephemeral",
            "workspace.write_tracked",
            "workspace.edit",
            "workspace.delete",
            "process.exec_bounded",
            "run.report_progress",
            "env.read",
            "web.search",
            "web.visit",
            "media.read",
        }

    def test_returns_failure_when_agent_missing(self) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(None)

        result = runner_module.execute_agent_effect(
            effect,
            self._config(),
            runner_module.AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
        )

        assert result == PipelineEvent.AGENT_FAILURE

    def test_execute_agent_effect_propagates_mcp_config_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        monkeypatch.setattr(
            effect_executor_module,
            "build_session_mcp_plan",
            lambda **_kwargs: (_ for _ in ()).throw(
                McpConfigError("fallback backend 'searxng' is not configured")
            ),
        )

        with pytest.raises(McpConfigError):
            runner_module.execute_agent_effect(
                effect,
                self._config(),
                runner_module.AgentExecutionDeps(
                    invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                    agent_invocation_error=AgentError,
                    agent_registry=registry,
                ),
                WorkspaceScope("/tmp/worktree"),
                display_context=make_display_context(),
            )

    @pytest.mark.parametrize(
        ("phase", "artifact_paths"),
        [
            (
                "development",
                (
                    ".agent/artifacts/development_result.json",
                    ".agent/DEVELOPMENT_RESULT.md",
                ),
            ),
            (
                "development_analysis",
                (
                    ".agent/artifacts/development_analysis_decision.json",
                    ".agent/DEVELOPMENT_ANALYSIS_DECISION.md",
                ),
            ),
        ],
    )
    def test_execute_agent_effect_removes_stale_phase_artifact_before_invocation(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
        phase: str,
        artifact_paths: tuple[str, ...],
    ) -> None:
        effect = InvokeAgentEffect(
            agent_name="ccs/mm",
            phase=phase,
            prompt_file="PROMPT.md",
        )
        prompt_file = tmp_path / "PROMPT.md"
        prompt_file.write_text("prompt", encoding="utf-8")
        stale_artifacts = [tmp_path / artifact_path for artifact_path in artifact_paths]
        for stale_artifact in stale_artifacts:
            stale_artifact.parent.mkdir(parents=True, exist_ok=True)
            stale_artifact.write_text('{"type":"stale"}', encoding="utf-8")

        monkeypatch.setattr(
            effect_executor_module,
            "start_mcp_server",
            lambda *_args, **_kwargs: _FakeBridge(),
        )
        monkeypatch.setattr(effect_executor_module, "shutdown_mcp_server", lambda _bridge: None)
        monkeypatch.setattr(
            effect_executor_module, "materialize_system_prompt", lambda **_kwargs: str(prompt_file)
        )

        result = runner_module.execute_agent_effect(
            effect,
            UnifiedConfig(),
            runner_module.AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=AgentError,
                agent_registry=runner_module.AgentRegistry,
            ),
            WorkspaceScope(tmp_path),
            display_context=make_display_context(),
            policy_bundle=_load_default_policy_bundle(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        for stale_artifact in stale_artifacts:
            assert not stale_artifact.exists()

    def test_execute_agent_effect_preserves_planning_artifacts_on_analysis_loopback(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        effect = InvokeAgentEffect(
            agent_name="planner",
            phase="planning",
            prompt_file="PROMPT.md",
            drain="planning",
        )
        _write_minimal_plan_artifacts(tmp_path, context="Loopback plan")
        _write_minimal_plan_draft(tmp_path, context="Loopback draft")
        (tmp_path / ".agent" / "artifacts" / "planning_analysis_decision.json").write_text(
            json.dumps(
                {
                    "type": "planning_analysis_decision",
                    "content": {
                        "status": "request_changes",
                        "summary": "Need revisions",
                        "what_came_up_short": ["issue"],
                        "how_to_fix": ["fix it"],
                    },
                }
            ),
            encoding="utf-8",
        )
        prompt_file = tmp_path / "PROMPT.md"
        prompt_file.write_text("Revise the plan", encoding="utf-8")

        monkeypatch.setattr(
            effect_executor_module,
            "start_mcp_server",
            lambda *_args, **_kwargs: _FakeBridge(),
        )
        monkeypatch.setattr(effect_executor_module, "shutdown_mcp_server", lambda _bridge: None)
        monkeypatch.setattr(
            effect_executor_module, "materialize_system_prompt", lambda **_kwargs: str(prompt_file)
        )

        result = runner_module.execute_agent_effect(
            effect,
            UnifiedConfig(),
            runner_module.AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=AgentError,
                agent_registry=_registry_factory(MagicMock()),
            ),
            WorkspaceScope(tmp_path),
            display_context=make_display_context(),
            state=PipelineState(phase="planning", previous_phase="planning_analysis"),
            policy_bundle=_load_default_policy_bundle(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert (tmp_path / ".agent" / "artifacts" / "plan.json").exists()
        assert (tmp_path / ".agent" / "artifacts" / ".plan_draft.json").exists()
        assert (tmp_path / ".agent" / "PLAN.md").exists()

    def test_execute_agent_effect_preserves_planning_artifacts_on_same_phase_retry(
        self,
        monkeypatch: MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        effect = InvokeAgentEffect(
            agent_name="planner",
            phase="planning",
            prompt_file="PROMPT.md",
            drain="planning",
        )
        _write_minimal_plan_artifacts(tmp_path, context="Retryable plan")
        _write_minimal_plan_draft(tmp_path, context="Retryable draft")
        prompt_file = tmp_path / "PROMPT.md"
        prompt_file.write_text("Revise the plan", encoding="utf-8")
        (tmp_path / ".agent" / "tmp").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".agent" / "tmp" / "last_retry_error_planning.txt").write_text(
            "PREVIOUS ATTEMPT FAILED: validation error during planning retry",
            encoding="utf-8",
        )

        monkeypatch.setattr(
            effect_executor_module,
            "start_mcp_server",
            lambda *_args, **_kwargs: _FakeBridge(),
        )
        monkeypatch.setattr(effect_executor_module, "shutdown_mcp_server", lambda _bridge: None)
        monkeypatch.setattr(
            effect_executor_module, "materialize_system_prompt", lambda **_kwargs: str(prompt_file)
        )

        result = runner_module.execute_agent_effect(
            effect,
            UnifiedConfig(),
            runner_module.AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=AgentError,
                agent_registry=_registry_factory(MagicMock()),
            ),
            WorkspaceScope(tmp_path),
            display_context=make_display_context(),
            state=PipelineState(phase="planning", previous_phase="planning"),
            policy_bundle=_load_default_policy_bundle(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert (tmp_path / ".agent" / "artifacts" / "plan.json").exists()
        assert (tmp_path / ".agent" / "artifacts" / ".plan_draft.json").exists()
        assert (tmp_path / ".agent" / "PLAN.md").exists()

    def test_materialize_prepared_prompt_preserves_resumed_planning_context(
        self,
        tmp_path: Path,
    ) -> None:
        bundle = _load_default_policy_bundle()
        _write_minimal_plan_artifacts(tmp_path, context="Prepared resumed plan")
        _write_minimal_plan_draft(tmp_path, context="Prepared resumed draft")
        (tmp_path / "PROMPT.md").write_text(
            "Resume the interrupted planning pass",
            encoding="utf-8",
        )
        effect = PreparePromptEffect(
            phase="planning",
            drain="planning",
            previous_phase=None,
        )
        state = PipelineState(
            phase="planning",
            previous_phase=None,
            checkpoint_saved_count=1,
        )

        runner_module.materialize_prepared_prompt(
            effect,
            bundle.pipeline,
            bundle.artifacts,
            WorkspaceScope(tmp_path),
            bundle.agents,
            state,
        )

        rendered = (tmp_path / ".agent" / "tmp" / "planning_prompt.md").read_text(encoding="utf-8")
        assert "PLANNING EDIT MODE" in rendered
        assert (tmp_path / ".agent" / "artifacts" / "plan.json").exists()
        assert (tmp_path / ".agent" / "artifacts" / ".plan_draft.json").exists()
        assert (tmp_path / ".agent" / "PLAN.md").exists()

    def test_materialize_agent_prompt_if_needed_preserves_resumed_planning_context(
        self,
        tmp_path: Path,
    ) -> None:
        bundle = _load_default_policy_bundle()
        workspace = FsWorkspace(tmp_path)
        _write_minimal_plan_artifacts(tmp_path, context="Resumed plan")
        _write_minimal_plan_draft(tmp_path, context="Resumed draft")
        (tmp_path / "PROMPT.md").write_text(
            "Resume the interrupted planning pass",
            encoding="utf-8",
        )
        effect = InvokeAgentEffect(
            agent_name="planner",
            phase="planning",
            prompt_file=".agent/tmp/planning_prompt.md",
            drain="planning",
        )
        state = PipelineState(
            phase="planning",
            previous_phase=None,
            checkpoint_saved_count=1,
        )
        registry = MagicMock()
        registry.get.return_value = None

        runner_module.materialize_agent_prompt_if_needed(
            effect,
            state,
            workspace,
            bundle,
            registry,
        )

        rendered = (tmp_path / ".agent" / "tmp" / "planning_prompt.md").read_text(encoding="utf-8")
        assert "PLANNING EDIT MODE" in rendered
        assert (tmp_path / ".agent" / "artifacts" / "plan.json").exists()
        assert (tmp_path / ".agent" / "artifacts" / ".plan_draft.json").exists()
        assert (tmp_path / ".agent" / "PLAN.md").exists()

    def test_materialize_agent_prompt_if_needed_resets_resumed_planning_context_when_prompt_changed(
        self,
        tmp_path: Path,
    ) -> None:
        bundle = _load_default_policy_bundle()
        workspace = FsWorkspace(tmp_path)
        _write_minimal_plan_artifacts(tmp_path, context="Resumed plan")
        _write_minimal_plan_draft(tmp_path, context="Resumed draft")
        (tmp_path / ".agent").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".agent" / "CURRENT_PROMPT.md").write_text(
            "Resume the interrupted planning pass",
            encoding="utf-8",
        )
        (tmp_path / "PROMPT.md").write_text(
            "Replace the plan with a different task",
            encoding="utf-8",
        )
        effect = InvokeAgentEffect(
            agent_name="planner",
            phase="planning",
            prompt_file=".agent/tmp/planning_prompt.md",
            drain="planning",
        )
        state = PipelineState(
            phase="planning",
            previous_phase=None,
            checkpoint_saved_count=1,
        )
        registry = MagicMock()
        registry.get.return_value = None

        runner_module.materialize_agent_prompt_if_needed(
            effect,
            state,
            workspace,
            bundle,
            registry,
        )

        rendered = (tmp_path / ".agent" / "tmp" / "planning_prompt.md").read_text(encoding="utf-8")
        assert "PLANNING MODE" in rendered
        assert "PLANNING EDIT MODE" not in rendered
        assert (tmp_path / ".agent" / "CURRENT_PROMPT.md").read_text(encoding="utf-8") == (
            "Replace the plan with a different task"
        )
        assert not (tmp_path / ".agent" / "artifacts" / "plan.json").exists()
        assert not (tmp_path / ".agent" / "artifacts" / ".plan_draft.json").exists()
        assert not (tmp_path / ".agent" / "PLAN.md").exists()

    def test_dynamic_ccs_agent_reaches_invocation(self, monkeypatch: MonkeyPatch) -> None:
        effect = InvokeAgentEffect(
            agent_name="ccs/mm",
            phase="development",
            prompt_file="PROMPT.md",
        )
        invoked: dict[str, object] = {}

        monkeypatch.setattr(
            effect_executor_module,
            "start_mcp_server",
            lambda *_args, **_kwargs: _FakeBridge(),
        )
        monkeypatch.setattr(effect_executor_module, "shutdown_mcp_server", lambda _bridge: None)
        monkeypatch.setattr(
            effect_executor_module, "materialize_system_prompt", lambda **_kwargs: "PROMPT.md"
        )

        def record_invoke(config: AgentConfig, *_args: object, **_kwargs: object) -> object:
            invoked["cmd"] = config.cmd
            invoked["transport"] = config.transport
            return iter(["line"])

        result = runner_module.execute_agent_effect(
            effect,
            UnifiedConfig(),
            runner_module.AgentExecutionDeps(
                invoke_agent=record_invoke,
                agent_invocation_error=AgentError,
                agent_registry=runner_module.AgentRegistry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert invoked == {"cmd": "ccs mm", "transport": AgentTransport.CLAUDE}

    def test_handles_invocation_error_gracefully(self, monkeypatch: MonkeyPatch) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        monkeypatch.setattr(
            effect_executor_module,
            "start_mcp_server",
            lambda *_args, **_kwargs: _FakeBridge(),
        )
        monkeypatch.setattr(effect_executor_module, "shutdown_mcp_server", lambda _bridge: None)
        monkeypatch.setattr(
            effect_executor_module, "materialize_system_prompt", lambda **_kwargs: "PROMPT.md"
        )

        def raising_invoke(*_args: object, **_kwargs: object) -> None:
            raise AgentError("boom")

        result = runner_module.execute_agent_effect(
            effect,
            self._config(),
            runner_module.AgentExecutionDeps(
                invoke_agent=raising_invoke,
                agent_invocation_error=AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
        )

        assert result == PipelineEvent.AGENT_FAILURE

    def test_handles_unexpected_error_as_failure(self, monkeypatch: MonkeyPatch) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        monkeypatch.setattr(
            effect_executor_module,
            "start_mcp_server",
            lambda *_args, **_kwargs: _FakeBridge(),
        )
        monkeypatch.setattr(effect_executor_module, "shutdown_mcp_server", lambda _bridge: None)
        monkeypatch.setattr(
            effect_executor_module, "materialize_system_prompt", lambda **_kwargs: "PROMPT.md"
        )

        def raising_value_error(*_args: object, **_kwargs: object) -> None:
            raise ValueError("boom")

        result = runner_module.execute_agent_effect(
            effect,
            self._config(),
            runner_module.AgentExecutionDeps(
                invoke_agent=raising_value_error,
                agent_invocation_error=AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
        )

        assert result == PipelineEvent.AGENT_FAILURE

    def test_starts_and_shuts_down_mcp_bridge_around_invocation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

        def fake_start_mcp_server(session: object, workspace: object, **_kwargs: object) -> object:
            bridge = FakeBridge()
            bridge.start()
            return bridge

        monkeypatch.setattr(
            runner_module,
            "start_mcp_server",
            fake_start_mcp_server,
        )

        seen_options: list[object] = []

        def record_invoke(*_args: object, **kwargs: object) -> object:
            seen_options.append(kwargs.get("options"))
            return iter(["line"])

        result = runner_module.execute_agent_effect(
            effect,
            self._config(),
            runner_module.AgentExecutionDeps(
                invoke_agent=record_invoke,
                agent_invocation_error=AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert started["value"] is True
        assert shutdown["value"] is True
        assert seen_options

    def test_starts_fresh_mcp_server_for_each_invocation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

        def fake_start_mcp_server(*_args: object, **_kwargs: object) -> object:
            marker = len(created)
            created.append(marker)
            return FakeBridge(marker)

        monkeypatch.setattr(effect_executor_module, "start_mcp_server", fake_start_mcp_server)

        first = runner_module.execute_agent_effect(
            effect,
            self._config(),
            runner_module.AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
        )
        second = runner_module.execute_agent_effect(
            effect,
            self._config(),
            runner_module.AgentExecutionDeps(
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=AgentError,
                agent_registry=registry,
            ),
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
        )

        assert first == PipelineEvent.AGENT_SUCCESS
        assert second == PipelineEvent.AGENT_SUCCESS
        assert created == [0, 1]
