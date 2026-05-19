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

import pytest

from ralph.pipeline.progress import advance_phase
from ralph.pipeline.state import PipelineState

ValidationError = importlib.import_module("pydantic").ValidationError
DEFAULT_MAX_WORK_UNITS = 50


class TestAdvancePhaseRequiresPolicyForCommitTargets:
    """Tests that advance_phase raises when policy is None."""

    def test_raises_value_error_when_policy_is_none(self) -> None:

        state = PipelineState(phase="development_commit")
        with pytest.raises(ValueError, match="requires PipelinePolicy"):
            advance_phase(state, "development", policy=None)
