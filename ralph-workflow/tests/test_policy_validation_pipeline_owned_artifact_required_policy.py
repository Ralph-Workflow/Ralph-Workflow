"""Unit tests for policy validation.

Tests cover:
- Valid agents.toml loading
- Missing drain binding raises PolicyValidationError
- Chain referencing unknown agent raises PolicyValidationError
- Empty chain list raises PolicyValidationError
- All six built-in drains are bound in default agents.toml
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

import pytest

from ralph.phases.required_artifacts import resolve_phase_required_artifact
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    ArtifactContract,
)

if TYPE_CHECKING:
    from pathlib import Path

ValidationError = importlib.import_module("pydantic").ValidationError
DEFAULT_MAX_WORK_UNITS = 50


class TestPipelineOwnedArtifactRequiredPolicy:
    """Tests for pipeline-owned required artifact behavior."""

    def test_default_policy_loads_with_development_phase_required(self, tmp_path: Path) -> None:
        """Default policy must load with development artifact requirement owned by pipeline."""
        bundle = load_policy(tmp_path / ".agent")
        assert bundle.pipeline.phases["development"].artifact_required is True, (
            "phases.development.artifact_required must be True in default policy"
        )

    def test_artifact_contract_rejects_phase_owned_artifact_required(self) -> None:
        """ArtifactContract must reject artifact_required because it belongs to pipeline.toml."""
        with pytest.raises(ValueError, match=r"pipeline\.toml"):
            ArtifactContract.model_validate(
                {
                    "drain": "development",
                    "artifact_type": "development_result",
                    "artifact_required": False,
                }
            )

    def test_artifact_contract_does_not_publish_retired_json_path_override(self) -> None:
        assert "artifact_json_path" not in ArtifactContract.model_json_schema()["properties"]

    def test_phase_required_artifact_uses_pipeline_owned_required_flag(
        self, tmp_path: Path
    ) -> None:
        """resolve_phase_required_artifact threads artifact_required from phase policy."""

        bundle = load_policy(tmp_path / ".agent")
        dev_ra = resolve_phase_required_artifact(
            bundle.pipeline,
            bundle.artifacts,
            phase="development",
            drain="development",
        )
        assert dev_ra is not None, "development phase must have a RequiredArtifact entry"
        assert dev_ra.artifact_required is True, (
            "RequiredArtifact for development must have artifact_required=True from pipeline"
        )
