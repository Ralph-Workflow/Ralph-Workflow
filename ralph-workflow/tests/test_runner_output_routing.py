"""Routing tests for pipeline runner display output."""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from ralph.config.models import AgentConfig
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import ExitSuccessEffect, InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import _FakeBridge as FakeBridge
from tests._pipeline_deps_factory import make_test_pipeline_deps

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ralph.agents.invoke import InvokeOptions
    from ralph.config.models import UnifiedConfig


pytestmark = pytest.mark.timeout_seconds(5)


def _load_default_policy_bundle() -> object:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


@pytest.fixture(autouse=True)
def _stub_workspace_scope_and_policy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(
        runner_module,
        "load_policy_or_die",
        lambda _path: _load_default_policy_bundle(),
    )


def _config(verbosity: int = 1) -> MagicMock:
    config = MagicMock()
    config.general.verbosity = verbosity
    return config


def _registry_factory(agent_config: AgentConfig) -> object:
    class RegistryInstance:
        def get(self, name: str) -> AgentConfig | None:
            del name
            return agent_config

    class Registry:
        @classmethod
        def from_config(cls, config: UnifiedConfig) -> object:
            del cls, config
            return RegistryInstance()

    return Registry


def _make_effect_sequence() -> list[object]:
    return [
        InvokeAgentEffect(agent_name="planning", phase="planning", prompt_file="planning.md"),
        InvokeAgentEffect(
            agent_name="development",
            phase="development",
            prompt_file="development.md",
        ),
        InvokeAgentEffect(
            agent_name="development_analysis",
            phase="development_analysis",
            prompt_file="development_analysis.md",
        ),
        ExitSuccessEffect(),
    ]


def _patch_common_runner_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner_module, "materialize_prompt_for_phase", lambda **_kwargs: "PROMPT.md"
    )
    monkeypatch.setattr(
        runner_module, "materialize_system_prompt", lambda **_kwargs: "SYSTEM_PROMPT.md"
    )
    monkeypatch.setattr(
        runner_module, "handle_phase", lambda *_args, **_kwargs: [PipelineEvent.AGENT_SUCCESS]
    )
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state, *_args, **_kwargs: None)


def _fast_materializers(
    workspace_scope: WorkspaceScope, effect: InvokeAgentEffect
) -> tuple[object, object]:
    def _fast_phase_materializer(*_args: object, **_kwargs: object) -> str:
        prompt_path = workspace_scope.root / ".agent" / "tmp" / f"{effect.phase}_prompt.md"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text("prompt", encoding="utf-8")
        return str(prompt_path)

    def _fast_system_materializer(*_args: object, **_kwargs: object) -> str:
        sys_path = workspace_scope.root / ".agent" / "tmp" / "system_prompt.md"
        sys_path.parent.mkdir(parents=True, exist_ok=True)
        sys_path.write_text("system", encoding="utf-8")
        return str(sys_path)

    return _fast_phase_materializer, _fast_system_materializer


def test_run_streams_transcript_output_without_dashboard(monkeypatch: pytest.MonkeyPatch) -> None:
    effects = [
        InvokeAgentEffect(agent_name="planning", phase="planning", prompt_file="planning.md"),
        ExitSuccessEffect(),
    ]
    state = PipelineState(phase="planning")
    rendered = io.StringIO()
    test_console = Console(file=rendered, force_terminal=True, no_color=False, width=120)
    policy_bundle = _load_default_policy_bundle()
    display = ParallelDisplay(
        make_display_context(console=test_console, env={}),
        pipeline_policy=policy_bundle.pipeline,
    )

    def stub_determine_effect(_state: object, _bundle: object) -> object:
        return effects.pop(0)

    def stub_reducer(
        current_state: PipelineState,
        _event: object,
        _policy: object,
        recovery: object | None = None,
    ) -> object:
        del recovery
        return current_state.copy_with(phase="complete"), None

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterable[object]:
        del config, prompt_file
        assert options is not None
        assert options.show_progress is False
        return iter(['{"content":"planning output"}\n'])

    def fake_execute_effect(
        effect: object,
        config: UnifiedConfig,
        workspace_scope: WorkspaceScope,
        display: ParallelDisplay,
        *,
        state: PipelineState | None = None,
        **kwargs: object,
    ) -> PipelineEvent:
        del kwargs
        assert isinstance(effect, InvokeAgentEffect)
        if effect.phase == "planning":
            plan_path = workspace_scope.root / ".agent" / "artifacts" / "plan.json"
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(
                '{"type":"plan","content":{"noop":true}}',
                encoding="utf-8",
            )
        phase_mat, sys_mat = _fast_materializers(workspace_scope, effect)
        registry = _registry_factory(AgentConfig(cmd="fake-agent"))
        pipeline_deps = make_test_pipeline_deps(
            display_context=display._ctx,
            bridge=FakeBridge(),
            registry_factory=registry.from_config,
            phase_prompt_materializer=phase_mat,
            system_prompt_materializer=sys_mat,
        )
        return effect_executor_module.execute_agent_effect(
            effect,
            config,
            pipeline_deps,
            workspace_scope,
            display=display,
            display_context=display._ctx,
            state=state,
            invoke_agent=fake_invoke_agent,
            agent_invocation_error=RuntimeError,
        )

    _patch_common_runner_dependencies(monkeypatch)
    monkeypatch.setattr(runner_module, "determine_effect_from_policy", stub_determine_effect)
    monkeypatch.setattr(runner_module, "reducer_reduce", stub_reducer)
    monkeypatch.setattr(runner_module, "execute_effect", fake_execute_effect)

    result = runner_module.run(
        _config(),
        initial_state=state,
        display=display,
        pipeline_deps=make_test_pipeline_deps(display._ctx),
    )

    output = rendered.getvalue()
    assert result == 0
    assert "[phase] ◆ planning" in output
    assert "[phase] complete" in output
    assert "Pipeline Complete" in output


def test_single_agent_visual_parity(monkeypatch: pytest.MonkeyPatch) -> None:
    effects = _make_effect_sequence()
    state = PipelineState(phase="planning")
    rendered = io.StringIO()
    policy_bundle = _load_default_policy_bundle()
    display = ParallelDisplay(
        make_display_context(
            console=Console(file=rendered, force_terminal=False, width=120),
            env={},
        ),
        pipeline_policy=policy_bundle.pipeline,
    )
    next_states = iter(
        [
            state.copy_with(phase="development"),
            state.copy_with(phase="development_analysis"),
            state.copy_with(phase="complete"),
        ]
    )

    def stub_determine_effect(_state: object, _bundle: object) -> object:
        return effects.pop(0)

    def stub_reducer(
        _state: PipelineState,
        _event: object,
        _policy: object,
        recovery: object | None = None,
    ) -> object:
        del recovery
        return next(next_states), None

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterable[object]:
        del config, prompt_file
        assert options is not None
        return iter(['{"content":"phase output"}\n'])

    def fake_execute_effect(
        effect: object,
        config: UnifiedConfig,
        workspace_scope: WorkspaceScope,
        display: ParallelDisplay,
        *,
        state: PipelineState | None = None,
        **kwargs: object,
    ) -> PipelineEvent:
        del kwargs
        assert isinstance(effect, InvokeAgentEffect)
        if effect.phase == "planning":
            plan_path = workspace_scope.root / ".agent" / "artifacts" / "plan.json"
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(
                '{"type":"plan","content":{"noop":true}}',
                encoding="utf-8",
            )
            handoff_path = workspace_scope.root / ".agent" / "PLAN.md"
            handoff_path.write_text("# Execution Plan\n\nNo execution work is required.\n")
        phase_mat, sys_mat = _fast_materializers(workspace_scope, effect)
        registry = _registry_factory(AgentConfig(cmd="fake-agent"))
        pipeline_deps = make_test_pipeline_deps(
            display_context=display._ctx,
            bridge=FakeBridge(),
            registry_factory=registry.from_config,
            phase_prompt_materializer=phase_mat,
            system_prompt_materializer=sys_mat,
        )
        return effect_executor_module.execute_agent_effect(
            effect,
            config,
            pipeline_deps,
            workspace_scope,
            display=display,
            display_context=display._ctx,
            state=state,
            invoke_agent=fake_invoke_agent,
            agent_invocation_error=RuntimeError,
        )

    _patch_common_runner_dependencies(monkeypatch)
    monkeypatch.setattr(runner_module, "determine_effect_from_policy", stub_determine_effect)
    monkeypatch.setattr(runner_module, "reducer_reduce", stub_reducer)
    monkeypatch.setattr(runner_module, "execute_effect", fake_execute_effect)

    result = runner_module.run(
        _config(),
        initial_state=state,
        display=display,
        pipeline_deps=make_test_pipeline_deps(display._ctx),
    )

    output = rendered.getvalue()
    assert result == 0
    assert "[phase] ◆ planning" in output
    assert "[phase] ◆ development" in output
    assert "[phase] development_analysis" in output
    assert "[phase] complete" in output
    assert "Pipeline completed successfully." in output


def test_run_notifies_dashboard_subscriber_after_reduce(monkeypatch: pytest.MonkeyPatch) -> None:
    effects = [
        InvokeAgentEffect(agent_name="planning", phase="planning", prompt_file="planning.md"),
        ExitSuccessEffect(),
    ]
    state = PipelineState(phase="planning")
    call_order: list[tuple[str, object]] = []

    class _Subscriber:
        def notify(self, state: PipelineState) -> None:
            call_order.append(("notify", state.phase))

    def stub_determine_effect(_state: object, _bundle: object) -> object:
        return effects.pop(0)

    def stub_reducer(
        current_state: PipelineState,
        _event: object,
        _policy: object,
        recovery: object | None = None,
    ) -> object:
        del recovery
        next_state = current_state.copy_with(phase="complete")
        call_order.append(("reduce", next_state.phase))
        return next_state, None

    def fake_execute_effect(
        effect: object,
        config: UnifiedConfig,
        workspace_scope: WorkspaceScope,
        display: ParallelDisplay,
        *,
        state: PipelineState | None = None,
        **kwargs: object,
    ) -> PipelineEvent:
        del config, workspace_scope, display, state, kwargs
        assert isinstance(effect, InvokeAgentEffect)
        return PipelineEvent.AGENT_SUCCESS

    _patch_common_runner_dependencies(monkeypatch)
    monkeypatch.setattr(runner_module, "determine_effect_from_policy", stub_determine_effect)
    monkeypatch.setattr(runner_module, "reducer_reduce", stub_reducer)
    monkeypatch.setattr(runner_module, "execute_effect", fake_execute_effect)
    monkeypatch.setattr(
        runner_module,
        "phase_event_after_agent_run",
        lambda **_kwargs: PipelineEvent.AGENT_SUCCESS,
    )

    display = ParallelDisplay(
        make_display_context(
            console=Console(file=io.StringIO(), force_terminal=False, width=120),
            env={},
        )
    )

    result = runner_module.run(
        _config(),
        initial_state=state,
        display=display,
        dashboard_subscriber=_Subscriber(),
        pipeline_deps=make_test_pipeline_deps(display._ctx),
    )

    assert result == 0
    assert call_order == [
        ("notify", "planning"),
        ("reduce", "complete"),
        ("notify", "complete"),
    ]


def test_handle_inline_effect_notifies_dashboard_subscriber_after_checkpoint_reduce() -> None:
    state = PipelineState(phase="planning")
    notified_phases: list[str] = []

    class _Subscriber:
        def notify(self, state: PipelineState) -> None:
            notified_phases.append(state.phase)

    new_state = runner_module.handle_inline_effect(
        effect=runner_module.SaveCheckpointEffect(),
        state=state,
        pipeline_policy=MagicMock(),
        artifacts_policy=MagicMock(),
        workspace_scope=WorkspaceScope(Path.cwd()),
        dashboard_subscriber=_Subscriber(),
    )

    assert isinstance(new_state, PipelineState)
    assert notified_phases == [new_state.phase]


def test_handle_inline_effect_notifies_dashboard_subscriber_after_prepare_prompt() -> None:
    state = PipelineState(phase="planning")
    notified_phases: list[str] = []

    class _Subscriber:
        def notify(self, state: PipelineState) -> None:
            notified_phases.append(state.phase)

    effect = runner_module.PreparePromptEffect(
        phase="planning",
        iteration=0,
        drain="planning",
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            runner_module,
            "materialize_prepared_prompt",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(runner_module.ckpt, "save", lambda _state, *_args, **_kwargs: None)
        new_state = runner_module.handle_inline_effect(
            effect=effect,
            state=state,
            pipeline_policy=MagicMock(),
            artifacts_policy=MagicMock(),
            workspace_scope=WorkspaceScope(Path.cwd()),
            dashboard_subscriber=_Subscriber(),
        )

    assert isinstance(new_state, PipelineState)
    assert notified_phases == [new_state.phase]
