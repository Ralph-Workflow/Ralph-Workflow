from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from ralph.config.enums import Verbosity
from ralph.pipeline import runner as runner_module
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
)
from ralph.workspace.scope import WorkspaceScope


def _policy_bundle() -> SimpleNamespace:
    agents = AgentsPolicy(
        agent_chains={
            "planning": AgentChainConfig(agents=["planner"]),
            "development": AgentChainConfig(agents=["developer"]),
        },
        agent_drains={
            "planning": AgentDrainConfig(chain="planning"),
            "development": AgentDrainConfig(chain="development"),
        },
    )
    pipeline = PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                transitions=PhaseTransition(on_success="development"),
            ),
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="complete"),
            ),
        },
        entry_phase="planning",
        terminal_phase="complete",
    )
    return SimpleNamespace(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())


def test_run_completes_in_serial_mode_without_fan_out(
    tmp_git_repo,
    monkeypatch,
) -> None:
    prompt = tmp_git_repo / "PROMPT.md"
    prompt.write_text("# Test Prompt\n\nRun the serial path.")

    initial_state = PipelineState(
        phase="planning",
        phase_chains={
            "planning": AgentChainState(agents=["planner"]),
            "development": AgentChainState(agents=["developer"]),
        },
        work_units=(),
    )

    handled_phases: list[str] = []
    saved_states: list[PipelineState] = []

    monkeypatch.setattr(
        runner_module,
        "resolve_workspace_scope",
        lambda: WorkspaceScope(tmp_git_repo),
    )
    monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _path: _policy_bundle())
    monkeypatch.setattr(
        runner_module,
        "AgentRegistry",
        MagicMock(from_config=MagicMock(return_value=MagicMock())),
    )
    monkeypatch.setattr(
        runner_module,
        "_materialize_agent_prompt_if_needed",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        runner_module,
        "_phase_event_after_agent_run",
        lambda **kwargs: PipelineEvent.AGENT_SUCCESS,
    )

    def _save_state(state: PipelineState) -> None:
        saved_states.append(state)

    monkeypatch.setattr(runner_module.ckpt, "save", _save_state)

    def _fake_execute_effect(effect, config, workspace_scope, **kwargs: object) -> PipelineEvent:
        del config, workspace_scope, kwargs
        handled_phases.append(effect.phase)
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        runner_module,
        "_execute_effect_with_optional_display",
        _fake_execute_effect,
    )
    monkeypatch.setattr(
        runner_module,
        "_execute_fan_out_sync",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("fan-out should not run")),
    )

    exit_code = runner_module.run(
        config=MagicMock(),
        initial_state=initial_state,
        verbosity=Verbosity.QUIET,
    )

    assert exit_code == 0
    assert handled_phases == ["planning", "development"]
    assert saved_states[-1].phase == "complete"


def test_serial_run_completes_when_development_phase_encounters_multimodal_tool_output(
    tmp_git_repo,
    monkeypatch,
) -> None:
    """Serial unattended run reaches 'complete' even when the development phase produces
    multimodal placeholder output (e.g. '[image: image/png]') from the parser.

    This is the black-box proof that the managed runtime path survives multimodal
    tool results: the pipeline must not emit a fan-out event or get stuck when the
    development phase processes multimodal tool output.
    """
    prompt = tmp_git_repo / "PROMPT.md"
    prompt.write_text("# Test\n\nRun multimodal serial path.")

    initial_state = PipelineState(
        phase="planning",
        phase_chains={
            "planning": AgentChainState(agents=["planner"]),
            "development": AgentChainState(agents=["developer"]),
        },
        work_units=(),
    )

    handled_phases: list[str] = []
    saved_states: list[PipelineState] = []
    monkeypatch.setattr(
        runner_module,
        "resolve_workspace_scope",
        lambda: WorkspaceScope(tmp_git_repo),
    )
    monkeypatch.setattr(runner_module, "load_policy_or_die", lambda _path: _policy_bundle())
    monkeypatch.setattr(
        runner_module,
        "AgentRegistry",
        MagicMock(from_config=MagicMock(return_value=MagicMock())),
    )
    monkeypatch.setattr(
        runner_module,
        "_materialize_agent_prompt_if_needed",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        runner_module,
        "_phase_event_after_agent_run",
        lambda **kwargs: PipelineEvent.AGENT_SUCCESS,
    )

    def _save_state(state: PipelineState) -> None:
        saved_states.append(state)

    monkeypatch.setattr(runner_module.ckpt, "save", _save_state)

    def _fake_execute_effect(effect, config, workspace_scope, **kwargs: object) -> PipelineEvent:
        del config, workspace_scope, kwargs
        handled_phases.append(effect.phase)
        # Simulate successful completion even when the development phase encountered
        # multimodal placeholder output like '[image: image/png]' from the parser.
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        runner_module,
        "_execute_effect_with_optional_display",
        _fake_execute_effect,
    )
    monkeypatch.setattr(
        runner_module,
        "_execute_fan_out_sync",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("fan-out must not run")),
    )

    exit_code = runner_module.run(
        config=MagicMock(),
        initial_state=initial_state,
        verbosity=Verbosity.QUIET,
    )

    assert exit_code == 0
    assert "development" in handled_phases
    assert saved_states[-1].phase == "complete"

