"""Tests for ralph/pipeline/runner.py — pipeline runner."""

from __future__ import annotations

import json
from functools import lru_cache
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from ralph.agents.invoke import AgentInvocationError
from ralph.config.enums import (
    AgentTransport,
    JsonParserType,
)
from ralph.config.mcp_loader import McpConfigError
from ralph.config.models import AgentConfig, CcsConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline import _runner_session as runner_session_module
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline import runner as runner_module
from ralph.pipeline.agent_retry_intent import resume_agent_retry_intent
from ralph.pipeline.effects import (
    InvokeAgentEffect,
    PreparePromptEffect,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_recording_bridge_factory, make_test_pipeline_deps
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


class TestExecuteAgentEffectA:
    @staticmethod
    def _config(verbosity: int = 2) -> MagicMock:
        config = MagicMock()
        config.general.verbosity = verbosity
        config.agents = {}
        config.ccs = CcsConfig()
        config.ccs_aliases = {"mm": "ccs mm"}
        return config

    def test_returns_success_when_invocation_succeeds(self) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        class FakeBridge:
            def shutdown(self) -> None:
                return

            def agent_endpoint_uri(self) -> str:
                return "http://127.0.0.1:12345/mcp"

            def reset_tool_registry(self) -> None:
                pass

        pipeline_deps = make_test_pipeline_deps(
            make_display_context(),
            bridge=FakeBridge(),
            registry_factory=registry.from_config,
        )

        result = effect_executor_module.execute_agent_effect(
            effect,
            self._config(),
            pipeline_deps,
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
            invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
            agent_invocation_error=AgentError,
        )

        assert result == PipelineEvent.AGENT_SUCCESS

    def test_invoke_start_records_visible_activity_on_display_subscriber(
        self, tmp_path: Path
    ) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        class FakeBridge:
            @property
            def run_id(self) -> str:
                return "fake-run-id"

            def shutdown(self) -> None:
                return

            def agent_endpoint_uri(self) -> str:
                return "http://127.0.0.1:12345/mcp"

            def reset_tool_registry(self) -> None:
                pass

        pipeline_deps = make_test_pipeline_deps(
            make_display_context(),
            bridge=FakeBridge(),
            registry_factory=registry.from_config,
        )

        console = Console(file=StringIO(), force_terminal=False, width=120, color_system=None)
        display = ParallelDisplay(
            make_display_context(console=console, env={"CI": "1"}, force_mode="medium"),
            workspace_root=tmp_path,
            run_id="run-invoke-start",
        )

        result = effect_executor_module.execute_agent_effect(
            effect,
            self._config(),
            pipeline_deps,
            WorkspaceScope(tmp_path),
            display=display,
            display_context=display.display_context,
            invoke_agent=lambda *_args, options=None, **_kwargs: (
                options.pre_output_listener() if options is not None else None,
                iter(()),
            )[1],
            agent_invocation_error=AgentError,
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        snapshot = display.subscriber.build_snapshot(PipelineState(phase="development"))
        assert snapshot is not None
        assert snapshot.active_agent == "dev"
        assert snapshot.last_activity_line in {
            "Invoking agent: dev",
            "Agent process started; waiting for first output",
        }

    def test_phase_banner_renders_before_invoke_start_activity(self, tmp_path: Path) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        class FakeBridge:
            @property
            def run_id(self) -> str:
                return "fake-run-id"

            def shutdown(self) -> None:
                return

            def agent_endpoint_uri(self) -> str:
                return "http://127.0.0.1:12345/mcp"

            def reset_tool_registry(self) -> None:
                pass

        pipeline_deps = make_test_pipeline_deps(
            make_display_context(),
            bridge=FakeBridge(),
            registry_factory=registry.from_config,
        )

        console = Console(
            file=StringIO(),
            force_terminal=False,
            width=160,
            color_system=None,
            record=True,
        )
        display = ParallelDisplay(
            make_display_context(console=console, env={"CI": "1"}, force_mode="medium"),
            workspace_root=tmp_path,
            run_id="run-phase-order",
        )
        state = PipelineState(phase="development", budget_caps={"iteration": 1})
        policy_bundle = _load_default_policy_bundle()

        display.emit_phase_start(
            effect.phase,
            agent_name=effect.agent_name,
            pipeline_policy=policy_bundle.pipeline,
        )

        result = effect_executor_module.execute_agent_effect(
            effect,
            self._config(),
            pipeline_deps,
            WorkspaceScope(tmp_path),
            display=display,
            display_context=display.display_context,
            state=state,
            policy_bundle=policy_bundle,
            invoke_agent=lambda *_args, **_kwargs: iter(()),
            agent_invocation_error=AgentError,
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        out = console.export_text()
        assert "Development" in out
        assert "Invoking agent: dev" in out
        assert out.index("Development") < out.index("Invoking agent: dev")

    def test_pre_output_progress_renders_when_agent_has_not_emitted_output_yet(
        self, tmp_path: Path
    ) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        class FakeBridge:
            @property
            def run_id(self) -> str:
                return "fake-run-id"

            def shutdown(self) -> None:
                return

            def agent_endpoint_uri(self) -> str:
                return "http://127.0.0.1:12345/mcp"

            def reset_tool_registry(self) -> None:
                pass

        pipeline_deps = make_test_pipeline_deps(
            make_display_context(),
            bridge=FakeBridge(),
            registry_factory=registry.from_config,
        )

        console = Console(
            file=StringIO(),
            force_terminal=False,
            width=160,
            color_system=None,
            record=True,
        )
        display = ParallelDisplay(
            make_display_context(console=console, env={"CI": "1"}, force_mode="medium"),
            workspace_root=tmp_path,
            run_id="run-pre-output",
        )

        result = effect_executor_module.execute_agent_effect(
            effect,
            self._config(),
            pipeline_deps,
            WorkspaceScope(tmp_path),
            display=display,
            display_context=display.display_context,
            invoke_agent=lambda *_args, options=None, **_kwargs: (
                options.pre_output_listener() if options is not None else None,
                iter(()),
            )[1],
            agent_invocation_error=AgentError,
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        out = console.export_text()
        assert "Invoking agent: dev" in out
        assert "Agent process started; waiting for first output" in out

    def test_development_session_gets_expected_mcp_capabilities(self) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())
        recording_factory = make_recording_bridge_factory(_FakeBridge())

        pipeline_deps = make_test_pipeline_deps(
            make_display_context(),
            bridge_factory=recording_factory,
            registry_factory=registry.from_config,
        )

        result = effect_executor_module.execute_agent_effect(
            effect,
            self._config(),
            pipeline_deps,
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
            invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
            agent_invocation_error=AgentError,
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        call = recording_factory.calls[-1]
        assert set(call["capabilities"]) == {
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
            "process.exec_unbounded",
            "run.report_progress",
            "env.read",
            "web.search",
            "web.visit",
            "web.download",
            "media.read",
        }

    def test_custom_phase_uses_bound_drain_capabilities(self) -> None:
        effect = InvokeAgentEffect(
            agent_name="dev",
            phase="custom_phase",
            prompt_file="PROMPT.md",
            drain="development",
        )
        registry = _registry_factory(MagicMock())
        recording_factory = make_recording_bridge_factory(_FakeBridge())

        pipeline_deps = make_test_pipeline_deps(
            make_display_context(),
            bridge_factory=recording_factory,
            registry_factory=registry.from_config,
        )

        result = effect_executor_module.execute_agent_effect(
            effect,
            self._config(),
            pipeline_deps,
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
            invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
            agent_invocation_error=AgentError,
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        call = recording_factory.calls[-1]
        assert call["drain"] == "development"
        assert set(call["capabilities"]) == {
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
            "process.exec_unbounded",
            "run.report_progress",
            "env.read",
            "web.search",
            "web.visit",
            "web.download",
            "media.read",
        }

    def test_returns_failure_when_agent_missing(self) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(None)

        pipeline_deps = make_test_pipeline_deps(
            make_display_context(),
            registry_factory=registry.from_config,
        )

        result = effect_executor_module.execute_agent_effect(
            effect,
            self._config(),
            pipeline_deps,
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
            invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
            agent_invocation_error=AgentError,
        )

        assert result == PipelineEvent.AGENT_FAILURE

    def test_execute_agent_effect_propagates_mcp_config_error(self) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        def failing_bridge_factory(**_kwargs: object) -> object:
            raise McpConfigError("fallback backend 'searxng' is not configured")

        pipeline_deps = make_test_pipeline_deps(
            make_display_context(),
            bridge_factory=failing_bridge_factory,
            registry_factory=registry.from_config,
        )

        with pytest.raises(McpConfigError):
            effect_executor_module.execute_agent_effect(
                effect,
                self._config(),
                pipeline_deps,
                WorkspaceScope("/tmp/worktree"),
                display_context=make_display_context(),
                invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
                agent_invocation_error=AgentError,
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

        pipeline_deps = make_test_pipeline_deps(
            make_display_context(),
            bridge=_FakeBridge(),
            system_prompt_materializer=lambda **_kwargs: str(prompt_file),
            registry_factory=runner_module.AgentRegistry.from_config,
        )

        result = effect_executor_module.execute_agent_effect(
            effect,
            UnifiedConfig(),
            pipeline_deps,
            WorkspaceScope(tmp_path),
            display_context=make_display_context(),
            invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
            agent_invocation_error=AgentError,
            policy_bundle=_load_default_policy_bundle(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        for stale_artifact in stale_artifacts:
            assert not stale_artifact.exists()

    def test_execute_agent_effect_preserves_planning_artifacts_on_analysis_loopback(
        self,
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

        pipeline_deps = make_test_pipeline_deps(
            make_display_context(),
            bridge=_FakeBridge(),
            system_prompt_materializer=lambda **_kwargs: str(prompt_file),
            registry_factory=_registry_factory(MagicMock()).from_config,
        )

        result = effect_executor_module.execute_agent_effect(
            effect,
            UnifiedConfig(),
            pipeline_deps,
            WorkspaceScope(tmp_path),
            display_context=make_display_context(),
            invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
            agent_invocation_error=AgentError,
            state=PipelineState(phase="planning", previous_phase="planning_analysis"),
            policy_bundle=_load_default_policy_bundle(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert (tmp_path / ".agent" / "artifacts" / "plan.json").exists()
        assert (tmp_path / ".agent" / "artifacts" / ".plan_draft.json").exists()
        assert (tmp_path / ".agent" / "PLAN.md").exists()

    def test_execute_agent_effect_worker_mode_uses_namespaced_system_prompt_and_session(
        self,
        tmp_path: Path,
    ) -> None:
        effect = InvokeAgentEffect(
            agent_name="developer",
            phase="development",
            prompt_file="PROMPT.md",
            drain="development",
        )
        worker_ns = tmp_path / ".agent" / "workers" / "unit-a"
        prompt_file = worker_ns / "tmp" / "development_system_prompt.md"
        captured: dict[str, object] = {}

        def _fake_materialize_system_prompt(**kwargs: object) -> str:
            captured["materialize_kwargs"] = kwargs
            return str(prompt_file)

        recording_factory = make_recording_bridge_factory(_FakeBridge())

        agent_config = AgentConfig(
            cmd="claude",
            output_flag="--json-stream",
            json_parser=JsonParserType.CLAUDE,
            transport=AgentTransport.CLAUDE,
        )

        pipeline_deps = make_test_pipeline_deps(
            make_display_context(),
            bridge_factory=recording_factory,
            system_prompt_materializer=_fake_materialize_system_prompt,
            registry_factory=_registry_factory(agent_config).from_config,
        )

        result = effect_executor_module.execute_agent_effect(
            effect,
            UnifiedConfig(),
            pipeline_deps,
            WorkspaceScope.for_same_workspace_worker(
                repo_root=tmp_path,
                allowed_directories=("src/a",),
                worker_namespace=worker_ns,
            ),
            display_context=make_display_context(),
            policy_bundle=_load_default_policy_bundle(),
            worker_namespace=worker_ns,
            worker_artifact_dir=worker_ns / "artifacts",
            parallel_worker=True,
            invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
            agent_invocation_error=AgentError,
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        materialize_kwargs = captured["materialize_kwargs"]
        assert isinstance(materialize_kwargs, dict)
        assert materialize_kwargs["worker_namespace"] == worker_ns
        call = recording_factory.calls[-1]
        assert call["parallel_worker"] is True
        assert call["worker_artifact_dir"] == worker_ns / "artifacts"
        assert call["worker_namespace"] == worker_ns
        assert worker_ns in call["allowed_roots"]

    def test_execute_agent_effect_worker_mode_does_not_clear_shared_phase_artifacts(
        self,
        tmp_path: Path,
    ) -> None:
        """Parallel workers must not touch shared repo-root phase outputs.

        The worker's workspace is write-restricted to its allowed directories
        plus its namespace; pre-run cleanup of the shared development_result
        artifact is the parent's job. The worker invocation must neither
        crash on the restricted scope nor delete the shared artifact.
        """
        effect = InvokeAgentEffect(
            agent_name="developer",
            phase="development",
            prompt_file="PROMPT.md",
            drain="development",
        )
        worker_ns = tmp_path / ".agent" / "workers" / "unit-a"
        shared_artifact = tmp_path / ".agent" / "artifacts" / "development_result.json"
        shared_artifact.parent.mkdir(parents=True, exist_ok=True)
        shared_artifact.write_text("{}", encoding="utf-8")
        prompt_file = worker_ns / "tmp" / "development_system_prompt.md"

        agent_config = AgentConfig(
            cmd="claude",
            output_flag="--json-stream",
            json_parser=JsonParserType.CLAUDE,
            transport=AgentTransport.CLAUDE,
        )

        pipeline_deps = make_test_pipeline_deps(
            make_display_context(),
            bridge=_FakeBridge(),
            system_prompt_materializer=lambda **_kwargs: str(prompt_file),
            registry_factory=_registry_factory(agent_config).from_config,
        )

        result = effect_executor_module.execute_agent_effect(
            effect,
            UnifiedConfig(),
            pipeline_deps,
            WorkspaceScope.for_same_workspace_worker(
                repo_root=tmp_path,
                allowed_directories=("src/a",),
                worker_namespace=worker_ns,
            ),
            display_context=make_display_context(),
            policy_bundle=_load_default_policy_bundle(),
            worker_namespace=worker_ns,
            worker_artifact_dir=worker_ns / "artifacts",
            parallel_worker=True,
            invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
            agent_invocation_error=AgentError,
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert shared_artifact.exists(), "worker must not clear shared phase artifacts"

    def test_execute_agent_effect_preserves_planning_artifacts_on_same_phase_retry(
        self,
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

        pipeline_deps = make_test_pipeline_deps(
            make_display_context(),
            bridge=_FakeBridge(),
            system_prompt_materializer=lambda **_kwargs: str(prompt_file),
            registry_factory=_registry_factory(MagicMock()).from_config,
        )

        result = effect_executor_module.execute_agent_effect(
            effect,
            UnifiedConfig(),
            pipeline_deps,
            WorkspaceScope(tmp_path),
            display_context=make_display_context(),
            invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
            agent_invocation_error=AgentError,
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

    def test_dynamic_ccs_agent_reaches_invocation(self) -> None:
        effect = InvokeAgentEffect(
            agent_name="ccs/mm",
            phase="development",
            prompt_file="PROMPT.md",
        )
        invoked: dict[str, object] = {}

        pipeline_deps = make_test_pipeline_deps(
            make_display_context(),
            bridge=_FakeBridge(),
            system_prompt_materializer=lambda **_kwargs: "PROMPT.md",
            registry_factory=runner_module.AgentRegistry.from_config,
        )

        def record_invoke(config: AgentConfig, *_args: object, **_kwargs: object) -> object:
            invoked["cmd"] = config.cmd
            invoked["transport"] = config.transport
            return iter(["line"])

        result = effect_executor_module.execute_agent_effect(
            effect,
            UnifiedConfig(),
            pipeline_deps,
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
            invoke_agent=record_invoke,
            agent_invocation_error=AgentError,
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert invoked == {"cmd": "ccs mm", "transport": AgentTransport.CLAUDE}

    def test_handles_invocation_error_gracefully(self) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        pipeline_deps = make_test_pipeline_deps(
            make_display_context(),
            bridge=_FakeBridge(),
            system_prompt_materializer=lambda **_kwargs: "PROMPT.md",
            registry_factory=registry.from_config,
        )

        def raising_invoke(*_args: object, **_kwargs: object) -> None:
            raise AgentError("boom")

        result = effect_executor_module.execute_agent_effect(
            effect,
            self._config(),
            pipeline_deps,
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
            invoke_agent=raising_invoke,
            agent_invocation_error=AgentError,
        )

        assert result == PipelineEvent.AGENT_FAILURE

    def test_execute_agent_effect_uses_canonical_retry_intent_resume(self) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        pipeline_deps = make_test_pipeline_deps(
            make_display_context(),
            bridge=_FakeBridge(),
            system_prompt_materializer=lambda **_kwargs: "PROMPT.md",
            registry_factory=registry.from_config,
        )

        seen_session_ids: list[str | None] = []

        def record_invoke(*_args: object, **kwargs: object) -> object:
            seen_session_ids.append(getattr(kwargs.get("options"), "session_id", None))
            return iter(["line"])

        state = PipelineState.model_validate({"phase": "development"}).copy_with(
            agent_retry_intent=resume_agent_retry_intent("sess-retry")
        )

        result = effect_executor_module.execute_agent_effect(
            effect,
            self._config(),
            pipeline_deps,
            WorkspaceScope("/tmp/worktree"),
            state=state,
            display_context=make_display_context(),
            invoke_agent=record_invoke,
            agent_invocation_error=AgentError,
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert seen_session_ids == ["sess-retry"]

    def test_apply_session_capture_clears_stale_session_after_fresh_retry_intent(self) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        pipeline_deps = make_test_pipeline_deps(
            make_display_context(),
            bridge=_FakeBridge(),
            system_prompt_materializer=lambda **_kwargs: "PROMPT.md",
            registry_factory=registry.from_config,
        )

        def stale_session_invoke(
            *_args: object,
            **_kwargs: object,
        ) -> object:
            yield '{"type":"session","session_id":"sess-stale"}'
            raise AgentInvocationError(
                "dev",
                1,
                "No conversation found with session ID: sess-stale",
            )

        state = PipelineState.model_validate({"phase": "development"}).copy_with(
            last_agent_session_id="sess-stale",
            agent_retry_intent=resume_agent_retry_intent("sess-stale"),
        )

        result = effect_executor_module.execute_agent_effect(
            effect,
            self._config(),
            pipeline_deps,
            WorkspaceScope("/tmp/worktree"),
            state=state,
            display_context=make_display_context(),
            invoke_agent=stale_session_invoke,
            agent_invocation_error=AgentInvocationError,
        )

        new_state = runner_session_module.apply_session_capture(state)

        assert result == PipelineEvent.AGENT_FAILURE
        assert new_state.last_agent_session_id is None
        assert new_state.agent_retry_intent.action == "fresh"

    def test_handles_unexpected_error_as_failure(self) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        pipeline_deps = make_test_pipeline_deps(
            make_display_context(),
            bridge=_FakeBridge(),
            system_prompt_materializer=lambda **_kwargs: "PROMPT.md",
            registry_factory=registry.from_config,
        )

        def raising_value_error(*_args: object, **_kwargs: object) -> None:
            raise ValueError("boom")

        result = effect_executor_module.execute_agent_effect(
            effect,
            self._config(),
            pipeline_deps,
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
            invoke_agent=raising_value_error,
            agent_invocation_error=AgentError,
        )

        assert result == PipelineEvent.AGENT_FAILURE

    def test_starts_and_shuts_down_mcp_bridge_around_invocation(self) -> None:
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

            def reset_tool_registry(self) -> None:
                pass

        def bridge_factory(**_kwargs: object) -> object:
            bridge = FakeBridge()
            bridge.start()
            return bridge

        pipeline_deps = make_test_pipeline_deps(
            make_display_context(),
            bridge_factory=bridge_factory,
            registry_factory=registry.from_config,
        )

        seen_options: list[object] = []

        def record_invoke(*_args: object, **kwargs: object) -> object:
            seen_options.append(kwargs.get("options"))
            return iter(["line"])

        result = effect_executor_module.execute_agent_effect(
            effect,
            self._config(),
            pipeline_deps,
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
            invoke_agent=record_invoke,
            agent_invocation_error=AgentError,
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert started["value"] is True
        assert shutdown["value"] is True
        assert seen_options

    def test_starts_fresh_mcp_server_for_each_invocation(self) -> None:
        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="PROMPT.md")
        registry = _registry_factory(MagicMock())

        created: list[int] = []

        class FakeBridge:
            @property
            def run_id(self) -> str:
                return "fake-run-id"

            def __init__(self, marker: int) -> None:
                self.marker = marker

            def shutdown(self) -> None:
                return

            def agent_endpoint_uri(self) -> str:
                return f"http://127.0.0.1:{12345 + self.marker}/mcp"

            def reset_tool_registry(self) -> None:
                pass

        def bridge_factory(**_kwargs: object) -> object:
            marker = len(created)
            created.append(marker)
            return FakeBridge(marker)

        pipeline_deps = make_test_pipeline_deps(
            make_display_context(),
            bridge_factory=bridge_factory,
            registry_factory=registry.from_config,
        )

        first = effect_executor_module.execute_agent_effect(
            effect,
            self._config(),
            pipeline_deps,
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
            invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
            agent_invocation_error=AgentError,
        )
        second = effect_executor_module.execute_agent_effect(
            effect,
            self._config(),
            pipeline_deps,
            WorkspaceScope("/tmp/worktree"),
            display_context=make_display_context(),
            invoke_agent=lambda *_args, **_kwargs: iter(["line"]),
            agent_invocation_error=AgentError,
        )

        assert first == PipelineEvent.AGENT_SUCCESS
        assert second == PipelineEvent.AGENT_SUCCESS
        assert created == [0, 1]
