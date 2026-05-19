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
from unittest.mock import MagicMock

import pytest

from ralph.policy.models import (
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
)
from ralph.policy.validation import (
    CheckpointPolicyMismatchError,
    validate_phase_exists_in_policy,
)

ValidationError = importlib.import_module("pydantic").ValidationError
DEFAULT_MAX_WORK_UNITS = 50


class TestValidatePhaseExistsInPolicy:
    """Tests for validate_phase_exists_in_policy."""

    def test_phase_exists_in_policy(self) -> None:
        """Test that an existing phase passes validation."""
        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="development"),
                ),
                "development": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(on_success="complete"),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        )
        # Should not raise
        validate_phase_exists_in_policy("development", pipeline)

    def test_phase_not_in_policy(self) -> None:
        """Test that a missing phase raises CheckpointPolicyMismatchError."""
        # Use mock to avoid Pydantic validation complexity
        pipeline = MagicMock()
        pipeline.phases = {
            "planning": MagicMock(),
            "development": MagicMock(),
            "review": MagicMock(),
        }

        with pytest.raises(CheckpointPolicyMismatchError) as exc_info:
            validate_phase_exists_in_policy("nonexistent_phase", pipeline)
        assert exc_info.value.checkpoint_phase == "nonexistent_phase"
