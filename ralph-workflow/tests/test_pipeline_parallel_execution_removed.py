"""Regression tests: global parallel_execution block has been removed."""

import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from ralph.pipeline.effect_router import determine_effect_from_policy
from ralph.pipeline.effects import FanOutEffect, InvokeAgentEffect
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.policy.loader import load_policy
from ralph.policy.models import PhaseParallelization, PipelinePolicy
from ralph.policy.validation import PolicyValidationError


def _load_default_bundle() -> object:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def test_loading_pipeline_toml_with_global_parallel_execution_block_raises(
    tmp_path: object,
) -> None:
    toml = tmp_path / "pipeline.toml"
    toml.write_text(
        textwrap.dedent("""\
            [parallel_execution]
            max_parallel_workers = 4
        """)
    )
    with pytest.raises(PolicyValidationError, match="parallel_execution"):
        load_policy(tmp_path)


def test_pipelinepolicy_model_rejects_parallel_execution_field() -> None:
    with pytest.raises(ValidationError, match="parallel_execution") as excinfo:
        PipelinePolicy.model_validate({"parallel_execution": {"max_parallel_workers": 2}})

    assert "regenerate-config" in str(excinfo.value)


def test_phase_definition_accepts_parallelization() -> None:
    max_workers = 4
    para = PhaseParallelization(mode="same_workspace", max_parallel_workers=max_workers)
    assert para.max_parallel_workers == max_workers
    assert para.mode == "same_workspace"


def test_parallelization_mode_must_be_same_workspace() -> None:
    with pytest.raises(ValidationError):
        PhaseParallelization.model_validate({"mode": "worktree"})


def test_default_pipeline_only_declares_parallelization_on_development() -> None:
    bundle = _load_default_bundle()
    for phase_name, phase_def in bundle.pipeline.phases.items():
        if phase_name == "development":
            assert phase_def.parallelization is not None, (
                "development phase must declare parallelization"
            )
        else:
            assert phase_def.parallelization is None, (
                f"phase {phase_name!r} must not declare parallelization"
            )


def test_default_pipeline_bundles_agent_subagents_dispatch_mode() -> None:
    """The bundled default sets ``dispatch_mode='agent_subagents'`` on the
    development phase so parallel execution is delegated to the AI agent's
    sub-agents. Ralph-managed fan-out is dormant.
    """
    bundle = _load_default_bundle()
    parallelization = bundle.pipeline.phases["development"].parallelization
    assert parallelization is not None
    assert parallelization.dispatch_mode == "agent_subagents"


def test_default_pipeline_dormant_default_falls_through_to_invoke_agent(tmp_path: Path) -> None:
    """The bundled default routes work_units through ``InvokeAgentEffect``
    (NOT ``FanOutEffect``) so the executing agent can dispatch its own
    sub-agents. ``test_pipeline_parallel_execution_removed.py`` is the
    canonical regression for this routing change.
    """
    bundle = _load_default_bundle()
    state = PipelineState(
        phase="development",
        work_units=(
            WorkUnit(unit_id="unit-a", description="A", allowed_directories=["src/a"]),
            WorkUnit(unit_id="unit-b", description="B", allowed_directories=["src/b"]),
        ),
    )
    config = MagicMock()
    config.agent_chains = {"developer": ["claude"]}
    config.agent_drains = {"development": "developer"}

    effect = determine_effect_from_policy(state, bundle, config=config)
    assert not isinstance(effect, FanOutEffect), (
        "Bundled default must NOT emit FanOutEffect when dispatch_mode="
        "'agent_subagents'; the executing agent dispatches its own sub-agents."
    )
    assert isinstance(effect, InvokeAgentEffect)
    assert effect.phase == "development"


def test_model_field_default_keeps_ralph_fan_out_for_backward_compat() -> None:
    """The ``PhaseParallelization`` model default is ``ralph_fan_out`` for
    backward compatibility with the 6 existing routing-test files (12
    positive isinstance assertions per PA-016) that build ``PolicyBundle``
    programmatically and assert ``FanOutEffect``. The bundled
    ``pipeline.toml`` overrides it to ``agent_subagents`` for production.
    """
    para = PhaseParallelization()
    assert para.dispatch_mode == "ralph_fan_out"
    para_explicit = PhaseParallelization(dispatch_mode="ralph_fan_out")
    assert para_explicit.dispatch_mode == "ralph_fan_out"
    para_subagents = PhaseParallelization(dispatch_mode="agent_subagents")
    assert para_subagents.dispatch_mode == "agent_subagents"
