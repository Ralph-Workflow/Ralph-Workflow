"""Regression tests: the bundled default falls through to ``InvokeAgentEffect``.

The bundled ``pipeline.toml`` sets ``dispatch_mode = 'agent_subagents'`` on
the development phase, so a plan that declares ``work_units`` must NOT
trigger Ralph-managed fan-out. The router logs a WARNING and falls through
to the single-agent ``InvokeAgentEffect`` so the executing agent can
dispatch its own sub-agents.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from loguru import logger as loguru_logger

from ralph.executor.process import ProcessResult
from ralph.pipeline import effect_router as effect_router_module
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effect_router import determine_effect_from_policy
from ralph.pipeline.effects import ExitFailureEffect, FanOutEffect, InvokeAgentEffect
from ralph.pipeline.factory import PipelineDeps
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseParallelization,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
)
from ralph.workspace.scope import WorkspaceScope
from tests._support.typed_accessors import (
    must_str_list,
)

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig


@lru_cache(maxsize=1)
def _default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _config_with_development_agent() -> UnifiedConfig:
    config = MagicMock()
    config.agent_chains = {"developer": ["claude"]}
    config.agent_drains = {"development": "developer"}
    return config


def _write_plan_artifact(root: Path, work_units: list[dict[str, object]]) -> None:
    artifact_dir = root / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    unit_items = "\n".join(
        f"- [{unit['unit_id']}] {unit['description']}\n"
        f"  Directories: {', '.join(must_str_list(unit['allowed_directories']))}"
        for unit in work_units
    )
    (artifact_dir / "plan.md").write_text(
        f"""---
type: plan
---
## Summary
Parallel development plan.

Intent: Implement independent work units.
Coverage: feature, test

## Scope
- [SC-1] Implement production changes
  Category: feature
- [SC-2] Add tests
  Category: test
- [SC-3] Verify the result
  Category: test

## Skills MCP
Skills: test-driven-development, verification-before-completion

## Steps

### [S-1] Implement
Do the work.

Type: file_change
Files:
- modify src/main.py
Verify: pytest tests/test_effect_router_dormant_fanout.py -q
Expect: the dormant fan-out tests pass with exit code 0

## Critical Files
- [CF-1] src/main.py
  Action: modify
  Changes: implement the feature

## Risks
- [R-1] Parallel changes overlap
  Severity: high
  Mitigation: Assign disjoint directories.

## Verification
- [V-1] pytest
  Expect: focused tests pass

## Work Units
{unit_items}
""",
        encoding="utf-8",
    )


def _two_disjoint_units() -> list[dict[str, object]]:
    return [
        {"unit_id": "unit-a", "description": "A", "allowed_directories": ["src/a"]},
        {"unit_id": "unit-b", "description": "B", "allowed_directories": ["src/b"]},
    ]


@pytest.mark.parametrize("agent_name", ["agy", "agy/gemini-3.6-flash-low"])
def test_effect_router_regression_agy_agent_subagents_without_available_agents_fails_explicitly(
    tmp_path: Path,
    agent_name: str,
) -> None:
    """Plan S-7: AGY must not silently fall back after its stock v1.1.8 probe found no agents."""
    _write_plan_artifact(tmp_path, _two_disjoint_units())
    state = PipelineState(phase="development")

    effect = determine_effect_from_policy(
        state,
        _default_policy_bundle(),
        WorkspaceScope(tmp_path),
        config=_config_with_development_agent(),
    )

    # Establish the existing non-AGY default before selecting AGY below.
    assert isinstance(effect, InvokeAgentEffect)
    agy_state = PipelineState(
        phase="development",
        phase_chains={"development": {"agents": [agent_name]}},
    )
    effect = determine_effect_from_policy(
        agy_state,
        _default_policy_bundle(),
        WorkspaceScope(tmp_path),
        config=_config_with_development_agent(),
        agy_agents_probe=lambda: "Available agents:\n",
    )

    assert isinstance(effect, ExitFailureEffect)
    assert effect.reason == (
        "AGY dispatch unavailable: `agy agents` reported no sub-agents on this install; "
        "configure an AGY sub-agent and retry."
    )


def test_agy_agents_probe_regression_is_bounded_and_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """DA-002: default AGY discovery must be bounded and reuse the first result."""
    calls: list[tuple[str, tuple[str, ...], effect_router_module.ProcessRunOptions]] = []

    def _run_process(
        command: str,
        args: tuple[str, ...],
        *,
        options: effect_router_module.ProcessRunOptions,
    ) -> ProcessResult:
        calls.append((command, args, options))
        return ProcessResult((command, *args), 0, "Available agents:\n- reviewer", "")

    monkeypatch.setattr(effect_router_module, "run_process", _run_process)
    probe = effect_router_module._make_default_agy_agents_probe()

    assert probe() == "Available agents:\n- reviewer"
    assert probe() == "Available agents:\n- reviewer"
    assert calls == [("agy", ("agents",), calls[0][2])]
    options = calls[0][2]
    assert options.timeout == 5.0


def test_runner_regression_forwards_agy_agents_probe_to_effect_router(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DA-002: the production runner must preserve the per-install AGY probe seam."""

    def expected_probe() -> str:
        return "Available agents:\n- reviewer"

    observed: dict[str, object] = {}

    def _router(
        state: PipelineState,
        policy_bundle: PolicyBundle,
        workspace_scope: WorkspaceScope,
        *,
        config: UnifiedConfig,
        agy_agents_probe: object | None = None,
    ) -> InvokeAgentEffect:
        del state, policy_bundle, workspace_scope, config
        observed["probe"] = agy_agents_probe
        return InvokeAgentEffect(
            agent_name="agy/gemini-3.6-flash-low",
            phase="development",
            prompt_file="PROMPT.md",
        )

    monkeypatch.setattr(runner_module, "determine_effect_from_policy", _router)
    effect = runner_module.call_determine_effect_from_policy(
        PipelineState(phase="development"),
        _default_policy_bundle(),
        WorkspaceScope(tmp_path),
        _config_with_development_agent(),
        pipeline_deps=PipelineDeps(
            display_context=MagicMock(),
            agy_agents_probe=expected_probe,
        ),
    )

    assert isinstance(effect, InvokeAgentEffect)
    assert observed["probe"] is expected_probe


def test_dormant_default_falls_through_to_invoke_agent_effect(tmp_path: Path) -> None:
    """Bundled default ``dispatch_mode='agent_subagents'`` must log a WARNING
    and fall through to ``InvokeAgentEffect`` for plans with 2+ work units.
    """
    _write_plan_artifact(tmp_path, _two_disjoint_units())
    state = PipelineState(phase="development")
    bundle = _default_policy_bundle()
    assert bundle.pipeline.phases["development"].parallelization.dispatch_mode == "agent_subagents"

    effect = determine_effect_from_policy(
        state,
        bundle,
        WorkspaceScope(tmp_path),
        config=_config_with_development_agent(),
    )

    assert not isinstance(effect, FanOutEffect), (
        "Bundled default must NOT emit FanOutEffect when dispatch_mode="
        "'agent_subagents'; the executing agent dispatches its own sub-agents."
    )
    assert isinstance(effect, InvokeAgentEffect)
    assert effect.phase == "development"


def test_dormant_default_logs_warning_with_real_string(tmp_path: Path) -> None:
    """The router MUST log the documented WARNING string when the bundled
    default is in effect and work_units are present.

    The audit at ``ralph.testing.audit_parallelization_dormant`` enforces the
    WARNING string in ``effect_router.py``; this test asserts the same string
    is actually emitted at runtime by intercepting the loguru sink.
    """
    _write_plan_artifact(tmp_path, _two_disjoint_units())
    state = PipelineState(phase="development")
    bundle = _default_policy_bundle()

    captured: list[tuple[str, str]] = []

    def _sink(message: object) -> None:
        record = message.record
        captured.append((record["level"].name, str(record["message"])))

    handler_id = loguru_logger.add(_sink, level="WARNING")
    try:
        determine_effect_from_policy(
            state,
            bundle,
            WorkspaceScope(tmp_path),
            config=_config_with_development_agent(),
        )
    finally:
        loguru_logger.remove(handler_id)

    warning_messages = [msg for level, msg in captured if level == "WARNING"]
    assert warning_messages, f"router must emit a WARNING when fan-out is dormant, got: {captured}"
    assert any(
        "Ralph-managed fan-out is dormant in this build" in msg for msg in warning_messages
    ), f"WARNING must include the documented string, got: {warning_messages}"


def test_dormant_default_single_unit_still_serial(tmp_path: Path) -> None:
    """A single work unit must still fall through to ``InvokeAgentEffect``
    under the bundled default (the 2-unit threshold is independent of
    dispatch_mode).
    """
    _write_plan_artifact(
        tmp_path,
        [
            {
                "unit_id": "solo",
                "description": "S",
                "allowed_directories": ["src"],
            }
        ],
    )
    state = PipelineState(phase="development")

    effect = determine_effect_from_policy(
        state,
        _default_policy_bundle(),
        WorkspaceScope(tmp_path),
        config=_config_with_development_agent(),
    )

    assert isinstance(effect, InvokeAgentEffect)
    assert effect.phase == "development"


def test_ralph_fan_out_mode_still_emits_fan_out_when_explicit() -> None:
    """When a phase declares ``dispatch_mode='ralph_fan_out'`` (the legacy
    mode) the router must still emit ``FanOutEffect`` for 2+ work units.
    This guards the opt-in path for future re-arming.
    """
    units = (
        WorkUnit(unit_id="unit-a", description="A", allowed_directories=["src/a"]),
        WorkUnit(unit_id="unit-b", description="B", allowed_directories=["src/b"]),
    )
    state = PipelineState(phase="development", work_units=units)
    dev_phase = PhaseDefinition(
        drain="development",
        role="execution",
        transitions=PhaseTransition(on_success="complete"),
        parallelization=PhaseParallelization(
            mode="same_workspace",
            dispatch_mode="ralph_fan_out",
            max_parallel_workers=2,
        ),
    )
    bundle = PolicyBundle(
        agents=AgentsPolicy(
            agent_chains={"developer": AgentChainConfig(agents=["claude"])},
            agent_drains={"development": AgentDrainConfig(chain="developer")},
        ),
        pipeline=PipelinePolicy(
            phases={"development": dev_phase},
            entry_phase="development",
            terminal_phase="complete",
        ),
        artifacts=ArtifactsPolicy(artifacts={}),
    )

    effect = determine_effect_from_policy(
        state,
        bundle,
        config=_config_with_development_agent(),
    )
    assert isinstance(effect, FanOutEffect)
    assert {u.unit_id for u in effect.work_units} == {"unit-a", "unit-b"}


def test_multi_unit_nested_steps_reach_ralph_fan_out_assignments(tmp_path: Path) -> None:
    """One Work Units section must route non-empty, unit-specific step assignments."""
    artifact_dir = tmp_path / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "plan.md").write_text(
        """---
type: plan
---
## Work Units
- [unit-a] Implement A
  Directories: src/a

### [S-1] Implement A
Type: file_change
Files:
- modify src/a/main.py
Verify: pytest tests/test_effect_router_dormant_fanout.py -q
Expect: the dormant fan-out tests pass with exit code 0

- [unit-b] Implement B
  Directories: src/b

### [S-2] Implement B
Type: file_change
Files:
- modify src/b/main.py
Verify: pytest tests/test_effect_router_dormant_fanout.py -q
Expect: the dormant fan-out tests pass with exit code 0
""",
        encoding="utf-8",
    )
    dev_phase = PhaseDefinition(
        drain="development",
        role="execution",
        transitions=PhaseTransition(on_success="complete"),
        parallelization=PhaseParallelization(
            mode="same_workspace",
            dispatch_mode="ralph_fan_out",
            max_parallel_workers=2,
        ),
    )
    bundle = PolicyBundle(
        agents=AgentsPolicy(
            agent_chains={"developer": AgentChainConfig(agents=["claude"])},
            agent_drains={"development": AgentDrainConfig(chain="developer")},
        ),
        pipeline=PipelinePolicy(
            phases={"development": dev_phase},
            entry_phase="development",
            terminal_phase="complete",
        ),
        artifacts=ArtifactsPolicy(artifacts={}),
    )

    effect = determine_effect_from_policy(
        PipelineState(phase="development"),
        bundle,
        WorkspaceScope(tmp_path),
        config=_config_with_development_agent(),
    )

    assert isinstance(effect, FanOutEffect)
    assert {unit.unit_id: unit.step_ids for unit in effect.work_units} == {
        "unit-a": ["S-1"],
        "unit-b": ["S-2"],
    }
