"""Fan-out routing must derive work units from the on-disk plan artifact.

The planning agent declares ``work_units`` inside the plan artifact. When the
development phase is routed and the pipeline state carries no work units, the
effect router must read the plan artifact and emit a ``FanOutEffect`` so that
multiple agent CLI instances actually run in parallel.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

from ralph.pipeline.effect_router import determine_effect_from_policy
from ralph.pipeline.effects import ExitFailureEffect, FanOutEffect, InvokeAgentEffect
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState, WorkerStatus
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.policy.models import PolicyBundle


@lru_cache(maxsize=1)
def _default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _legacy_fan_out_policy_bundle() -> PolicyBundle:
    """Bundle that opts into the legacy ``ralph_fan_out`` dispatch mode.

    The bundled default in ``ralph/policy/defaults/pipeline.toml`` sets
    ``dispatch_mode = 'agent_subagents'`` on the development phase, so
    the existing routing tests that assert ``FanOutEffect`` must opt
    into the legacy path explicitly on their policy fixture.
    """
    bundle = _default_policy_bundle()
    dev_phase = bundle.pipeline.phases["development"]
    assert dev_phase.parallelization is not None
    legacy_parallelization = dev_phase.parallelization.model_copy(
        update={"dispatch_mode": "ralph_fan_out"}
    )
    legacy_dev_phase = dev_phase.model_copy(update={"parallelization": legacy_parallelization})
    legacy_phases = dict(bundle.pipeline.phases)
    legacy_phases["development"] = legacy_dev_phase
    return bundle.model_copy(
        update={"pipeline": bundle.pipeline.model_copy(update={"phases": legacy_phases})}
    )


def _config_with_development_agent() -> UnifiedConfig:
    config = MagicMock()
    config.agent_chains = {"developer": ["claude"]}
    config.agent_drains = {"development": "developer"}
    return cast("UnifiedConfig", config)


def _plan_document(work_units: list[dict[str, object]]) -> str:
    unit_items = "\n".join(
        f"- [{unit['unit_id']}] {unit['description']}\n"
        f"  Directories: {', '.join(cast('list[str]', unit['allowed_directories']))}"
        for unit in work_units
    )
    return f"""---
type: plan
schema_version: 1
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
  Expect: passes

## Work Units
{unit_items}
"""


def _write_plan_artifact(root: Path, document: str) -> None:
    artifact_dir = root / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "plan.md").write_text(document, encoding="utf-8")


def _two_disjoint_units() -> list[dict[str, object]]:
    return [
        {"unit_id": "unit-a", "description": "A", "allowed_directories": ["src/a"]},
        {"unit_id": "unit-b", "description": "B", "allowed_directories": ["src/b"]},
    ]


def _five_nested_work_units_plan() -> str:
    sections = []
    for number, name in enumerate(("api", "web", "docs", "contract", "integration"), start=1):
        sections.append(
            f"""## Work Units
- [{name}] Implement the {name} unit
  Directories: src/{name}

### [S-{number}] Implement {name}
Change the {name} component.

Type: file_change
Files:
- modify src/{name}/main.py
"""
        )
    return "---\ntype: plan\n---\n" + "\n".join(sections)


def test_development_phase_fans_out_from_plan_artifact_work_units(tmp_path: Path) -> None:
    _write_plan_artifact(tmp_path, _plan_document(_two_disjoint_units()))
    state = PipelineState(phase="development")
    legacy_bundle = _legacy_fan_out_policy_bundle()

    effect = determine_effect_from_policy(
        state,
        legacy_bundle,
        WorkspaceScope(tmp_path),
        config=_config_with_development_agent(),
    )

    assert isinstance(effect, FanOutEffect)
    assert {u.unit_id for u in effect.work_units} == {"unit-a", "unit-b"}
    parallelization = legacy_bundle.pipeline.phases["development"].parallelization
    assert parallelization is not None
    assert effect.max_workers == parallelization.max_parallel_workers


def test_fanout_regression_routes_five_units_with_nested_step_assignments(
    tmp_path: Path,
) -> None:
    """Regression for plan blocker 6: routing retains every unit's mini-plan."""
    _write_plan_artifact(tmp_path, _five_nested_work_units_plan())

    effect = determine_effect_from_policy(
        PipelineState(phase="development"),
        _legacy_fan_out_policy_bundle(),
        WorkspaceScope(tmp_path),
        config=_config_with_development_agent(),
    )

    assert isinstance(effect, FanOutEffect)
    assert [(unit.unit_id, unit.step_ids) for unit in effect.work_units] == [
        ("api", ["S-1"]),
        ("web", ["S-2"]),
        ("docs", ["S-3"]),
        ("contract", ["S-4"]),
        ("integration", ["S-5"]),
    ]


def test_single_plan_work_unit_falls_back_to_serial_agent(tmp_path: Path) -> None:
    _write_plan_artifact(
        tmp_path,
        _plan_document([{"unit_id": "solo", "description": "S", "allowed_directories": ["src"]}]),
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


def test_missing_plan_artifact_falls_back_to_serial_agent(tmp_path: Path) -> None:
    state = PipelineState(phase="development")

    effect = determine_effect_from_policy(
        state,
        _default_policy_bundle(),
        WorkspaceScope(tmp_path),
        config=_config_with_development_agent(),
    )

    assert isinstance(effect, InvokeAgentEffect)
    assert effect.phase == "development"


def test_noop_plan_falls_back_to_serial_agent(tmp_path: Path) -> None:
    _write_plan_artifact(tmp_path, "---\ntype: plan\nnoop: true\n---\n")
    state = PipelineState(phase="development")

    effect = determine_effect_from_policy(
        state,
        _default_policy_bundle(),
        WorkspaceScope(tmp_path),
        config=_config_with_development_agent(),
    )

    assert isinstance(effect, InvokeAgentEffect)
    assert effect.phase == "development"


def test_corrupted_plan_artifact_falls_back_to_serial_agent(tmp_path: Path) -> None:
    artifact_dir = tmp_path / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "plan.md").write_text("not a plan", encoding="utf-8")
    state = PipelineState(phase="development")

    effect = determine_effect_from_policy(
        state,
        _default_policy_bundle(),
        WorkspaceScope(tmp_path),
        config=_config_with_development_agent(),
    )

    assert isinstance(effect, InvokeAgentEffect)
    assert effect.phase == "development"


def test_preseeded_single_unit_state_ignores_plan_artifact(tmp_path: Path) -> None:
    """A worker child carries exactly one unit in state and must stay serial."""
    _write_plan_artifact(
        tmp_path,
        _plan_document(
            [
                *_two_disjoint_units(),
                {"unit_id": "unit-c", "description": "C", "allowed_directories": ["src/c"]},
            ]
        ),
    )
    state = PipelineState(
        phase="development",
        work_units=(WorkUnit(unit_id="unit-a", description="A", allowed_directories=["src/a"]),),
    )

    effect = determine_effect_from_policy(
        state,
        _default_policy_bundle(),
        WorkspaceScope(tmp_path),
        config=_config_with_development_agent(),
    )

    assert isinstance(effect, InvokeAgentEffect)
    assert effect.phase == "development"


def test_non_parallelized_phase_ignores_plan_work_units(tmp_path: Path) -> None:
    _write_plan_artifact(tmp_path, _plan_document(_two_disjoint_units()))
    state = PipelineState(phase="planning")
    config = MagicMock()
    config.agent_chains = {"planner": ["claude"]}
    config.agent_drains = {"planning": "planner"}

    effect = determine_effect_from_policy(
        state,
        _default_policy_bundle(),
        WorkspaceScope(tmp_path),
        config=cast("UnifiedConfig", config),
    )

    assert isinstance(effect, InvokeAgentEffect)
    assert effect.phase == "planning"


def test_resume_with_recorded_worker_states_still_fans_out(tmp_path: Path) -> None:
    """After checkpoint resume the plan on disk must re-trigger fan-out."""
    _write_plan_artifact(tmp_path, _plan_document(_two_disjoint_units()))
    state = PipelineState(
        phase="development",
        worker_states={
            "unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.SUCCEEDED),
        },
    )

    effect = determine_effect_from_policy(
        state,
        _legacy_fan_out_policy_bundle(),
        WorkspaceScope(tmp_path),
        config=_config_with_development_agent(),
    )

    assert isinstance(effect, FanOutEffect)
    assert {u.unit_id for u in effect.work_units} == {"unit-a", "unit-b"}


def test_overlapping_plan_work_unit_directories_are_rejected(tmp_path: Path) -> None:
    """The pre-flight overlap rejection only runs on the legacy
    ``ralph_fan_out`` path. Under the bundled default
    (``dispatch_mode='agent_subagents'``) the router falls through to
    ``InvokeAgentEffect`` so the executing agent can validate the
    work-unit directories itself. This test exercises the legacy
    path's overlap rejection.
    """
    _write_plan_artifact(
        tmp_path,
        _plan_document(
            [
                {"unit_id": "unit-a", "description": "A", "allowed_directories": ["src"]},
                {"unit_id": "unit-b", "description": "B", "allowed_directories": ["src/sub"]},
            ]
        ),
    )
    state = PipelineState(phase="development")

    effect = determine_effect_from_policy(
        state,
        _legacy_fan_out_policy_bundle(),
        WorkspaceScope(tmp_path),
        config=_config_with_development_agent(),
    )

    assert isinstance(effect, ExitFailureEffect)
    assert "parallel preflight rejected plan" in effect.reason
