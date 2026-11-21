from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.enums import Verbosity
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.phases.required_artifacts import resolve_phase_required_artifact
from ralph.pipeline import runner as runner_module
from ralph.pipeline.no_progress_reroute import OVERRIDDEN_SUCCESS_LIMIT
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import cached_default_policy_bundle
from ralph.prompts.materialize import materialize_prompt_for_phase
from ralph.recovery.canary_probe import CANARY_PROMPT_FILENAME
from ralph.recovery.controller import RecoveryController
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.invoke import InvokeOptions
    from ralph.config.agent_config import AgentConfig

pytestmark = [pytest.mark.timeout_seconds(20)]

_PLAN = (
    "---\n"
    "type: plan\n"
    "---\n"
    "# Plan\n"
    "1. This is a complete test plan with more than ten words in order to satisfy the validator.\n"
)


class _Registry:
    def get(self, name: str) -> AgentConfig:
        from ralph.config.models import AgentConfig

        return AgentConfig(cmd=name)


def _unresolved_integration(
    _config: UnifiedConfig, _scope: WorkspaceScope, _rebase: RebaseState
) -> RebaseState:
    return RebaseState(
        last_action="rebase",
        last_reason="the conflict resolver did not produce a resolution",
        integration_unresolved=True,
    )


def test_a_success_overridden_after_every_run_advances_within_the_bound(
    tmp_path: Path,
) -> None:
    bundle = cached_default_policy_bundle()
    runner_module.register_role_handlers(bundle.pipeline)
    (tmp_path / "PROMPT.md").write_text("Implement the change.\n", encoding="utf-8")
    invoked: list[str] = []

    def fake_invoke_agent(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
        _clock: object = None,
    ) -> Iterator[str]:
        del config, options, _clock
        prompt = Path(prompt_file)
        if prompt.name == CANARY_PROMPT_FILENAME:
            return iter(["ok\n"])
        invoked.append(prompt.name)
        plan = tmp_path / ".agent" / "artifacts" / "plan.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(_PLAN, encoding="utf-8")
        return iter(["planned\n"])

    context = make_display_context()
    deps = replace(
        make_test_pipeline_deps(
            context,
            policy_bundle=bundle,
            registry_factory=lambda _cfg: _Registry(),
            artifact_requirements_resolver=resolve_phase_required_artifact,
            invoke_agent=fake_invoke_agent,
        ),
        phase_prompt_materializer=materialize_prompt_for_phase,
        auto_integrate_resolver=_unresolved_integration,
    )
    config = UnifiedConfig.model_validate({"general": {"max_same_agent_retries": 10}})
    controller = RecoveryController()
    state = PipelineState.from_policy(bundle.pipeline)
    for _step in range(OVERRIDDEN_SUCCESS_LIMIT * 4):
        result = runner_module.run_pipeline_step(
            state=state,
            config=config,
            workspace_scope=WorkspaceScope(tmp_path),
            policy_bundle=bundle,
            registry=_Registry(),
            display=ParallelDisplay(context),
            display_context=context,
            verbosity=Verbosity.QUIET,
            pipeline_deps=deps,
            recovery_controller=controller,
            pipeline_subscriber=None,
        )
        assert isinstance(result, PipelineState)
        state = result.copy_with(rebase=RebaseState())
        if state.phase != "planning":
            break

    assert state.phase == "planning_analysis"
    assert len(invoked) == OVERRIDDEN_SUCCESS_LIMIT
