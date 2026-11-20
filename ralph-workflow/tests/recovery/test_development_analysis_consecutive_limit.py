from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import NoReturn

from ralph.agents.invoke import OpenCodeResumableExitError
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import Verbosity
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.phases.required_artifacts import resolve_phase_required_artifact
from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effect_executor import (
    install_agent_effect_dependencies,
    reset_agent_effect_dependencies,
)
from ralph.pipeline.events import AgentFailureEvent
from ralph.pipeline.runner import checkpoint_path_for_root
from ralph.recovery.agent_failure_ledger import Cooldown, Eligible
from ralph.recovery.unavailability_reason import UnavailabilityReason
from ralph.workspace.awareness import awareness_for_workspace, release_workspace_awareness
from ralph.workspace.fs import FsWorkspace
from tests.recovery.test_cooldown_real_invocation_main_phases import _MainPhaseHarness

_PHASE = "development_analysis"
_ATTEMPTS = 5


def _opencode_harness(root: Path) -> tuple[_MainPhaseHarness, list[tuple[str, object]]]:
    harness = _MainPhaseHarness(_PHASE, root)
    invocations: list[tuple[str, object]] = []

    def invoke_agent(
        agent_config: AgentConfig,
        prompt_file: str,
        *,
        options: object = None,
        **_kwargs: object,
    ) -> NoReturn:
        assert options is not None
        agent = str(getattr(options, "agent_name", ""))
        probe = getattr(options, "workspace_probe", None)
        sink = getattr(options, "failure_observation_sink", None)
        assert probe is not None
        assert callable(sink)
        invocations.append((agent, harness.controller.agent_eligibility(_PHASE, agent)))
        workspace = FsWorkspace(root)
        awareness = awareness_for_workspace(root)
        try:
            awareness.set_watch_active()
            probe.observe_before(_PHASE, workspace)
            ckpt.save(harness.state, checkpoint_path_for_root(root))
            awareness.record(str(checkpoint_path_for_root(root)))
            changed = probe.has_changed(_PHASE, workspace)
            sink(
                AgentFailureEvent(
                    phase=_PHASE,
                    agent=agent,
                    artifact_valid=False,
                    workspace_changed=changed,
                    reason="OpenCodeResumableExitError",
                )
            )
        finally:
            release_workspace_awareness(root)
        raise OpenCodeResumableExitError(agent, returncode=0)

    harness.config = UnifiedConfig(
        agents={
            name: AgentConfig(
                cmd="opencode run",
                transport="opencode",
                json_parser="opencode",
                output_flag="--format json",
            )
            for name in harness.agents
        }
    )
    harness.registry = AgentRegistry.from_config(harness.config)
    harness.deps = replace(
        harness.deps,
        invoke_agent=invoke_agent,
        artifact_requirements_resolver=resolve_phase_required_artifact,
    )
    return harness, invocations


def _run_step(harness: _MainPhaseHarness) -> None:
    token = install_agent_effect_dependencies(
        recovery_controller=harness.controller,
        pipeline_deps=harness.deps,
    )
    try:
        harness.state = runner_module.run_pipeline_step(
            state=harness.state,
            policy_bundle=harness.bundle,
            workspace_scope=harness.scope,
            config=harness.config,
            display=harness.display,
            display_context=harness.display_context,
            verbosity=Verbosity.QUIET,
            registry=harness.registry,
            pipeline_subscriber=None,
            recovery_controller=harness.controller,
            pipeline_deps=harness.deps,
        )
    finally:
        reset_agent_effect_dependencies(token)


def test_five_artifactless_development_analysis_attempts_cool_the_agent_at_the_third(
    tmp_git_repo: Path,
) -> None:
    harness, invocations = _opencode_harness(tmp_git_repo)
    limit = harness.controller.ledger.consecutive_limit

    for _step in range(_ATTEMPTS):
        if len(invocations) >= _ATTEMPTS:
            break
        _run_step(harness)

    agents = [agent for agent, _status in invocations[:_ATTEMPTS]]
    assert limit == 3
    assert agents == [harness.primary] * limit + [harness.agents[1]] * (_ATTEMPTS - limit)
    assert all(isinstance(status, Eligible) for _agent, status in invocations[:limit])
    status = harness.controller.agent_eligibility(_PHASE, harness.primary)
    assert isinstance(status, Cooldown)
    assert status.reason == str(UnavailabilityReason.UNPRODUCTIVE_LIMIT)
    entry = harness.controller.ledger.snapshot()["entries"]
    assert isinstance(entry, dict)
    assert entry[f"{_PHASE}:{harness.primary}"]["cooldown_count"] == 1
