"""Regression tests: the bundled default falls through to ``InvokeAgentEffect``.

The bundled ``pipeline.toml`` sets ``dispatch_mode = 'agent_subagents'`` on
the development phase, so a plan that declares ``work_units`` must NOT
trigger Ralph-managed fan-out. The router logs a WARNING and falls through
to the single-agent ``InvokeAgentEffect`` so the executing agent can
dispatch its own sub-agents.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

from loguru import logger as loguru_logger

from ralph.pipeline.effect_router import determine_effect_from_policy
from ralph.pipeline.effects import FanOutEffect, InvokeAgentEffect
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
    return cast("UnifiedConfig", config)


def _write_plan_artifact(root: Path, work_units: list[dict[str, object]]) -> None:
    artifact_dir = root / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "plan.json").write_text(
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": "Parallel plan",
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    },
                    "skills_mcp": {"skills": ["test-driven-development"], "mcps": []},
                    "steps": [
                        {"number": 1, "title": "Implement", "content": "do the work"}
                    ],
                    "critical_files": {
                        "primary_files": [{"path": "src/main.py", "action": "modify"}],
                        "reference_files": [],
                    },
                    "risks_mitigations": [{"risk": "drift", "mitigation": "verify"}],
                    "verification_strategy": [
                        {"method": "pytest", "expected_outcome": "passes"}
                    ],
                    "work_units": work_units,
                },
            }
        ),
        encoding="utf-8",
    )


def _two_disjoint_units() -> list[dict[str, object]]:
    return [
        {"unit_id": "unit-a", "description": "A", "allowed_directories": ["src/a"]},
        {"unit_id": "unit-b", "description": "B", "allowed_directories": ["src/b"]},
    ]


def test_dormant_default_falls_through_to_invoke_agent_effect(tmp_path: Path) -> None:
    """Bundled default ``dispatch_mode='agent_subagents'`` must log a WARNING
    and fall through to ``InvokeAgentEffect`` for plans with 2+ work units.
    """
    _write_plan_artifact(tmp_path, _two_disjoint_units())
    state = PipelineState(phase="development")
    bundle = _default_policy_bundle()
    assert (
        bundle.pipeline.phases["development"].parallelization.dispatch_mode
        == "agent_subagents"
    )

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
