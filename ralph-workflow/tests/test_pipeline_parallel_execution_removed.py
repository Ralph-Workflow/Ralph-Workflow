"""Regression tests: global parallel_execution block has been removed."""

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from ralph.policy.loader import PolicyValidationError, load_policy
from ralph.policy.models import PhaseParallelization, PipelinePolicy


def _load_default_bundle():
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def test_loading_pipeline_toml_with_global_parallel_execution_block_raises(
    tmp_path,
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
