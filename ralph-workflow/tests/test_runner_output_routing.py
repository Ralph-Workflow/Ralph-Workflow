"""Routing tests for pipeline runner display output."""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from ralph.config.models import AgentConfig
from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import ExitSuccessEffect, InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ralph.agents.invoke import InvokeOptions
    from ralph.config.models import UnifiedConfig


def _load_default_policy_bundle():
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


def _registry_factory(agent_config: AgentConfig):
    class RegistryInstance:
        def get(self, name: str) -> AgentConfig | None:
            del name
            return agent_config

    class Registry:
        @classmethod
        def from_config(cls, config: UnifiedConfig):
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
        InvokeAgentEffect(agent_name="review", phase="review", prompt_file="review.md"),
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
        runner_module,
        "start_mcp_server",
        lambda *_args, **_kwargs: type(
            "FakeBridge",
            (),
            {"agent_endpoint_uri": lambda self: "http://127.0.0.1:12345/mcp"},
        )(),
    )
    monkeypatch.setattr(runner_module, "shutdown_mcp_server", lambda _bridge: None)
    monkeypatch.setattr(
        runner_module, "handle_phase", lambda *_args, **_kwargs: [PipelineEvent.AGENT_SUCCESS]
    )
    monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)


def test_run_streams_transcript_output_without_dashboard(monkeypatch: pytest.MonkeyPatch) -> None:
    effects = [
        InvokeAgentEffect(agent_name="planning", phase="planning", prompt_file="planning.md"),
        ExitSuccessEffect(),
    ]
    state = PipelineState(phase="planning")
    rendered = io.StringIO()
    test_console = Console(file=rendered, force_terminal=True, width=120)
    display = ParallelDisplay(test_console, env={})

    def stub_determine_effect(_state: object, _bundle: object) -> object:
        return effects.pop(0)

    def stub_reducer(current_state: PipelineState, _event: object, _policy: object):
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
    ) -> PipelineEvent:
        assert isinstance(effect, InvokeAgentEffect)
        deps = runner_module._AgentExecutionDeps(
            invoke_agent=fake_invoke_agent,
            agent_invocation_error=RuntimeError,
            agent_registry=_registry_factory(AgentConfig(cmd="fake-agent")),
        )
        return runner_module._execute_agent_effect(
            effect, config, deps, workspace_scope, display=display, state=state
        )

    _patch_common_runner_dependencies(monkeypatch)
    monkeypatch.setattr(runner_module, "_determine_effect_from_policy", stub_determine_effect)
    monkeypatch.setattr(runner_module, "reducer_reduce", stub_reducer)
    monkeypatch.setattr(runner_module, "_execute_effect", fake_execute_effect)

    result = runner_module.run(_config(), initial_state=state, display=display)

    output = rendered.getvalue()
    assert result == 0
    assert "Invoking agent: planning" in output
    assert "planning output" in output
    assert "Pipeline completed successfully." in output


def test_single_agent_visual_parity(monkeypatch: pytest.MonkeyPatch) -> None:
    effects = _make_effect_sequence()
    state = PipelineState(phase="planning")
    rendered = io.StringIO()
    display = ParallelDisplay(Console(file=rendered, force_terminal=False, width=120), env={})
    next_states = iter(
        [
            state.copy_with(phase="development"),
            state.copy_with(phase="review"),
            state.copy_with(phase="complete"),
        ]
    )

    def stub_determine_effect(_state: object, _bundle: object) -> object:
        return effects.pop(0)

    def stub_reducer(_state: PipelineState, _event: object, _policy: object):
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
    ) -> PipelineEvent:
        assert isinstance(effect, InvokeAgentEffect)
        deps = runner_module._AgentExecutionDeps(
            invoke_agent=fake_invoke_agent,
            agent_invocation_error=RuntimeError,
            agent_registry=_registry_factory(AgentConfig(cmd="fake-agent")),
        )
        return runner_module._execute_agent_effect(
            effect, config, deps, workspace_scope, display=display, state=state
        )

    _patch_common_runner_dependencies(monkeypatch)
    monkeypatch.setattr(runner_module, "_determine_effect_from_policy", stub_determine_effect)
    monkeypatch.setattr(runner_module, "reducer_reduce", stub_reducer)
    monkeypatch.setattr(runner_module, "_execute_effect", fake_execute_effect)

    result = runner_module.run(_config(), initial_state=state, display=display)

    output = rendered.getvalue()
    assert result == 0
    assert "Invoking agent: planning" in output
    assert "Invoking agent: development" in output
    assert "Invoking agent: review" in output
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

    def stub_reducer(current_state: PipelineState, _event: object, _policy: object):
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
    ) -> PipelineEvent:
        del config, workspace_scope, display, state
        assert isinstance(effect, InvokeAgentEffect)
        return PipelineEvent.AGENT_SUCCESS

    _patch_common_runner_dependencies(monkeypatch)
    monkeypatch.setattr(runner_module, "_determine_effect_from_policy", stub_determine_effect)
    monkeypatch.setattr(runner_module, "reducer_reduce", stub_reducer)
    monkeypatch.setattr(runner_module, "_execute_effect", fake_execute_effect)
    monkeypatch.setattr(
        runner_module,
        "_phase_event_after_agent_run",
        lambda **_kwargs: PipelineEvent.AGENT_SUCCESS,
    )

    display = ParallelDisplay(
        Console(file=io.StringIO(), force_terminal=False, width=120),
        env={},
    )

    result = runner_module.run(
        _config(),
        initial_state=state,
        display=display,
        dashboard_subscriber=_Subscriber(),
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

    new_state = runner_module._handle_inline_effect(
        effect=runner_module.SaveCheckpointEffect(),
        state=state,
        pipeline_policy=MagicMock(),
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
            "_materialize_prepared_prompt",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(runner_module.ckpt, "save", lambda _state: None)
        new_state = runner_module._handle_inline_effect(
            effect=effect,
            state=state,
            pipeline_policy=MagicMock(),
            workspace_scope=WorkspaceScope(Path.cwd()),
            dashboard_subscriber=_Subscriber(),
        )

    assert isinstance(new_state, PipelineState)
    assert notified_phases == [new_state.phase]
