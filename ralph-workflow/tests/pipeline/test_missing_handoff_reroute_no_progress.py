from __future__ import annotations

import re
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from loguru import logger

from ralph.config.enums import Verbosity
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.phases.required_artifacts import resolve_phase_required_artifact, retry_hint_path
from ralph.pipeline import checkpoint, run_loop
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import PreparePromptEffect
from ralph.pipeline.no_progress_reroute import NO_PROGRESS_REROUTE_LIMIT
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactContract,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
    RecoveryPolicy,
)
from ralph.prompts.materialize import materialize_prompt_for_phase
from ralph.recovery.canary_probe import CANARY_PROMPT_FILENAME
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import UnavailabilityReason
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.invoke import InvokeOptions
    from ralph.config.agent_config import AgentConfig

_MISSING_HANDOFF_LOG = "Missing plan handoff"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class _Registry:
    def get(self, name: str) -> AgentConfig:
        from ralph.config.models import AgentConfig

        return AgentConfig(cmd=name)


class _StopRun(KeyboardInterrupt):
    pass


@lru_cache(maxsize=1)
def _default_bundle() -> PolicyBundle:
    return load_policy(Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults")


def _development_entry_bundle() -> PolicyBundle:
    agents_policy = AgentsPolicy(
        agent_chains={
            "dev": AgentChainConfig(agents=["dev_agent"]),
            "terminal": AgentChainConfig(agents=["cooling_agent"]),
        },
        agent_drains={
            "development": AgentDrainConfig(chain="dev"),
            "terminal": AgentDrainConfig(chain="terminal"),
        },
    )
    phases = {
        "development": PhaseDefinition(
            drain="development",
            role="execution",
            artifact_required=True,
            prompt_template="developer_iteration.jinja",
            transitions=PhaseTransition(on_success="complete"),
        ),
        "complete": PhaseDefinition(
            drain="development",
            role="terminal",
            transitions=PhaseTransition(on_success="complete"),
        ),
        "failed_terminal": PhaseDefinition(
            drain="terminal",
            role="terminal",
            terminal_outcome="failure",
            transitions=PhaseTransition(on_success="failed_terminal"),
        ),
    }
    pipeline_policy = PipelinePolicy(
        phases=phases,
        entry_phase="development",
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )
    artifacts_policy = ArtifactsPolicy(
        artifacts={
            "development_result": ArtifactContract(
                drain="development",
                artifact_type="development_result",
                artifact_path=".agent/artifacts/development_result.md",
            ),
        }
    )
    return PolicyBundle(agents=agents_policy, pipeline=pipeline_policy, artifacts=artifacts_policy)


def _run(
    tmp_path: Path,
    bundle: PolicyBundle,
    initial_state: PipelineState,
    prompts: list[str],
    *,
    controller: RecoveryController | None = None,
) -> tuple[int, list[str]]:
    runner_module.register_role_handlers(bundle.pipeline)

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
        _clock: object = None,
    ) -> Iterator[str]:
        del config, options, _clock
        path = Path(prompt_file)
        if path.name == CANARY_PROMPT_FILENAME:
            return iter(["ok\n"])
        prompts.append((path if path.is_absolute() else tmp_path / path).read_text("utf-8"))
        raise _StopRun

    deps = make_test_pipeline_deps(
        make_display_context(),
        policy_bundle=bundle,
        registry_factory=lambda _cfg: _Registry(),
        artifact_requirements_resolver=resolve_phase_required_artifact,
        invoke_agent=fake_invoke_agent,
    )
    deps = replace(deps, phase_prompt_materializer=materialize_prompt_for_phase)
    if controller is not None:
        deps = replace(deps, recovery_controller_factory=lambda _s, _b, _c: (controller, 0))
    config = UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": False, "max_same_agent_retries": 0}}
    )
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level="WARNING")
    try:
        with (
            patch(
                "ralph.pipeline.runner.resolve_workspace_scope",
                return_value=WorkspaceScope(tmp_path),
            ),
            patch("ralph.pipeline.runner.write_start_commit_if_absent"),
            patch("ralph.pipeline.runner.validate_custom_mcp_servers", return_value=0),
        ):
            exit_code = run_loop.run(
                config,
                initial_state=initial_state,
                pipeline_deps=deps,
                verbosity=Verbosity.QUIET,
            )
    finally:
        logger.remove(sink)
    return exit_code, [line for line in lines if _MISSING_HANDOFF_LOG in line]


@pytest.mark.timeout_seconds(10)
def test_planning_retry_without_a_plan_invokes_the_agent_with_the_fresh_template(
    tmp_path: Path,
) -> None:
    bundle = _default_bundle()
    (tmp_path / "PROMPT.md").write_text("Implement the change.\n", encoding="utf-8")
    hint = tmp_path / retry_hint_path("planning", pipeline_policy=bundle.pipeline)
    hint.parent.mkdir(parents=True, exist_ok=True)
    hint.write_text("Publication gate refused the tree; fix the failures.\n", encoding="utf-8")
    retry_state = PipelineState.from_policy(bundle.pipeline, previous_phase="planning")
    prompts: list[str] = []

    _run(tmp_path, bundle, retry_state, prompts)

    assert len(prompts) == 1
    assert "PLANNING EDIT MODE" not in prompts[0]
    assert "Publication gate refused the tree" in prompts[0]
    saved = checkpoint.load(tmp_path / "checkpoint.json")
    assert saved is None or saved.recovery_epoch <= 1


@pytest.mark.timeout_seconds(10)
def test_a_missing_handoff_loop_in_another_phase_fails_within_the_bound(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = _development_entry_bundle()
    (tmp_path / "PROMPT.md").write_text("Implement the change.\n", encoding="utf-8")
    controller = RecoveryController(options=RecoveryControllerOptions())
    controller.mark_agent_unavailable(
        "failed_terminal", "cooling_agent", reason=UnavailabilityReason.UNPRODUCTIVE_LIMIT
    )
    state = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(agents=["dev_agent"]),
            "failed_terminal": AgentChainState(agents=["cooling_agent"]),
        },
        policy_entry_phase="development",
    )
    prompts: list[str] = []

    exit_code, reroutes = _run(tmp_path, bundle, state, prompts, controller=controller)

    assert exit_code == 1
    assert prompts == []
    assert len(reroutes) == NO_PROGRESS_REROUTE_LIMIT + 1
    output = " ".join(_ANSI.sub("", capsys.readouterr().out).split())
    error_cause = output.split("Error Cause", 1)[1].split("Metrics", 1)[0]
    assert "phase development cannot start" in error_cause
    assert ".agent/PLAN.md" in error_cause
    assert "all agents unavailable" not in error_cause


@pytest.mark.timeout_seconds(10)
def test_the_reroute_before_failing_clears_the_stale_retry_state(tmp_path: Path) -> None:
    bundle = _development_entry_bundle()
    (tmp_path / "PROMPT.md").write_text("Implement the change.\n", encoding="utf-8")
    hint = tmp_path / retry_hint_path("development", pipeline_policy=bundle.pipeline)
    hint.parent.mkdir(parents=True, exist_ok=True)
    hint.write_text("Previous attempt submitted no artifact.\n", encoding="utf-8")
    state = PipelineState(phase="development", policy_entry_phase="development")
    phases_seen: list[str] = []
    hint_present: list[bool] = []

    for _attempt in range(NO_PROGRESS_REROUTE_LIMIT + 1):
        result = runner_module.handle_inline_effect(
            effect=PreparePromptEffect(phase="development"),
            state=state,
            pipeline_policy=bundle.pipeline,
            artifacts_policy=bundle.artifacts,
            workspace_scope=WorkspaceScope(tmp_path),
        )
        assert isinstance(result, PipelineState)
        state = result
        phases_seen.append(str(state.phase))
        hint_present.append(hint.exists())

    assert phases_seen == ["development"] * NO_PROGRESS_REROUTE_LIMIT + ["failed_terminal"]
    assert hint_present == [True] * (NO_PROGRESS_REROUTE_LIMIT - 1) + [False, False]
    assert state.last_error is not None
    assert "phase development cannot start" in state.last_error
