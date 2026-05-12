"""Regression harness for workflow-run memory retention on the real CLI wrapper path."""

from __future__ import annotations

import gc
import json
import tracemalloc
from contextlib import nullcontext
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from ralph.cli.commands import run as run_module
from ralph.config.enums import JsonParserType, Verbosity
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import runner
from ralph.pipeline.effects import ExitSuccessEffect, InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
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
    from pathlib import Path

    from pytest import MonkeyPatch


_LINE_COUNT = 128
_LINE_SIZE = 8192
_ITERATION_COUNT = 20
_EXPECTED_PHASE_INVOCATIONS = _ITERATION_COUNT * 3
_RETAINED_DELTA_LIMIT = 2_000_000
_PEAK_DELTA_LIMIT = 16_000_000


def _policy_bundle() -> PolicyBundle:
    return PolicyBundle(
        agents=AgentsPolicy(
            agent_chains={
                "planning": AgentChainConfig(agents=["generic-agent"]),
                "development": AgentChainConfig(agents=["generic-agent"]),
                "development_analysis": AgentChainConfig(agents=["generic-agent"]),
                "complete": AgentChainConfig(agents=["generic-agent"]),
            },
            agent_drains={
                "planning": AgentDrainConfig(chain="planning"),
                "development": AgentDrainConfig(chain="development"),
                "development_analysis": AgentDrainConfig(chain="development_analysis"),
                "complete": AgentDrainConfig(chain="complete"),
            },
        ),
        pipeline=PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="development"),
                ),
                "development": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(on_success="development_analysis"),
                ),
                "development_analysis": PhaseDefinition(
                    drain="development_analysis",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        ),
        artifacts=ArtifactsPolicy(artifacts={}),
    )


def _install_display_context(monkeypatch: MonkeyPatch) -> None:
    console = Console(record=True, force_terminal=False, width=120, color_system=None)
    ctx = make_display_context(console=console, force_width=120, force_mode="wide")
    monkeypatch.setattr(run_module, "make_display_context", lambda **_kwargs: ctx)
    monkeypatch.setattr(runner, "make_display_context", lambda **_kwargs: ctx)


class _RegistryInstance:
    def __init__(self, agent_config: AgentConfig) -> None:
        self._agent_config = agent_config

    def get(self, _name: str) -> AgentConfig | None:
        return self._agent_config


class _RegistryFactory:
    @classmethod
    def from_config(cls, _config: UnifiedConfig) -> _RegistryInstance:
        return _RegistryInstance(
            AgentConfig(
                cmd="generic-agent",
                output_flag="--json-stream",
                json_parser=JsonParserType.GENERIC,
            )
        )


def _configure_workspace(monkeypatch: MonkeyPatch, tmp_path: Path) -> WorkspaceScope:
    (tmp_path / ".agent").mkdir(parents=True, exist_ok=True)
    (tmp_path / "PROMPT.md").write_text("# Goal\n\nMeasure memory retention.\n", encoding="utf-8")
    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr(run_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(runner, "resolve_workspace_scope", lambda: scope)
    return scope


def _configure_run_pipeline(monkeypatch: MonkeyPatch, tmp_path: Path) -> PolicyBundle:
    _configure_workspace(monkeypatch, tmp_path)
    policy_bundle = _policy_bundle()
    monkeypatch.setattr(run_module, "load_config", lambda *args, **kwargs: UnifiedConfig())
    monkeypatch.setattr(
        run_module,
        "load_policy_for_workspace_scope",
        lambda *args, **kwargs: policy_bundle,
    )
    monkeypatch.setattr(run_module, "_run_preflight_checks", lambda *args, **kwargs: 0)
    monkeypatch.setattr(runner, "load_policy_or_die", lambda *args, **kwargs: policy_bundle)
    monkeypatch.setattr(runner, "AgentRegistry", _RegistryFactory)
    monkeypatch.setattr(
        runner,
        "_materialize_agent_prompt_if_needed",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        runner,
        "_phase_event_after_agent_run",
        lambda **_kwargs: PipelineEvent.AGENT_SUCCESS,
    )
    monkeypatch.setattr(runner.ckpt, "save", lambda _state: None)
    monkeypatch.setattr(
        runner,
        "start_mcp_server",
        lambda *args, **kwargs: SimpleNamespace(agent_endpoint_uri=lambda: "http://127.0.0.1/mcp"),
    )
    monkeypatch.setattr(runner, "shutdown_mcp_server", lambda _bridge: None)
    monkeypatch.setattr(runner, "check_mcp_bridge_health", lambda _bridge: None)
    monkeypatch.setattr(runner, "McpSupervisor", lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(runner, "materialize_system_prompt", lambda **kwargs: None)
    monkeypatch.setattr(
        runner,
        "build_session_mcp_plan",
        lambda **kwargs: SimpleNamespace(
            capabilities=(),
            server_env={},
            model_identity=None,
            capability_profile=None,
        ),
    )
    return policy_bundle


def _install_runner_effect_seams(monkeypatch: MonkeyPatch, tmp_path: Path) -> list[str]:
    generated_effects = [
        InvokeAgentEffect(
            agent_name="generic-agent",
            phase=phase,
            prompt_file=str(tmp_path / "PROMPT.md"),
        )
        for _ in range(_ITERATION_COUNT)
        for phase in ("planning", "development", "development_analysis")
    ]
    consumed_phases: list[str] = []

    def determine_effect(*_args, **_kwargs):
        if generated_effects:
            return generated_effects.pop(0)
        return ExitSuccessEffect()

    def reduce_state(state: PipelineState, _event: object):
        next_phase = generated_effects[0].phase if generated_effects else "complete"
        return PipelineState(phase=next_phase), []

    def fake_invoke_agent(
        _config: AgentConfig,
        _prompt_file: str,
        *,
        options: object | None = None,
    ):
        del options
        phase = consumed_phases[-1]
        session_line = json.dumps({"session_id": f"sess-{phase}"})
        payload = "x" * _LINE_SIZE
        for idx in range(_LINE_COUNT):
            prefix = session_line if idx == 0 else f"{phase}:{idx}:"
            yield f"{prefix}{payload}"

    def execute_effect(effect, config, workspace_scope, **kwargs):
        if isinstance(effect, InvokeAgentEffect):
            consumed_phases.append(effect.phase)
            deps = runner._AgentExecutionDeps(
                invoke_agent=fake_invoke_agent,
                agent_invocation_error=runner.AgentInvocationError,
                agent_registry=_RegistryFactory,
            )
            return runner._execute_agent_effect(
                effect,
                config,
                deps,
                workspace_scope,
                display=kwargs.get("display"),
                verbosity=kwargs.get("verbosity", Verbosity.NORMAL),
                state=kwargs.get("state"),
                policy_bundle=kwargs.get("policy_bundle"),
            )
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(runner, "_determine_effect_from_policy", determine_effect)
    monkeypatch.setattr(runner, "reducer_reduce", reduce_state)
    monkeypatch.setattr(runner, "_execute_effect", execute_effect)
    return consumed_phases


@pytest.mark.integration
def test_run_pipeline_memory_regression(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    _install_display_context(monkeypatch)
    _configure_run_pipeline(monkeypatch, tmp_path)
    # Warm imports and patched path once before measuring.
    warmup_phases = _install_runner_effect_seams(monkeypatch, tmp_path)
    assert run_module.run_pipeline(counter_overrides={"iteration": 1}) == 0
    assert warmup_phases

    consumed_phases = _install_runner_effect_seams(monkeypatch, tmp_path)

    gc.collect()
    tracemalloc.start()
    baseline_current, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()

    exit_code = run_module.run_pipeline(counter_overrides={"iteration": _ITERATION_COUNT})

    gc.collect()
    final_current, peak_current = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    retained_delta_bytes = final_current - baseline_current
    peak_delta_bytes = peak_current - baseline_current

    assert exit_code == 0
    assert len(consumed_phases) == _EXPECTED_PHASE_INVOCATIONS
    assert retained_delta_bytes <= _RETAINED_DELTA_LIMIT
    assert peak_delta_bytes <= _PEAK_DELTA_LIMIT
