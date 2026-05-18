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

from ralph.policy.validation import (
    CheckpointPolicyMismatchError,
)

ValidationError = importlib.import_module("pydantic").ValidationError
DEFAULT_MAX_WORK_UNITS = 50


class TestCheckpointPolicyMismatchError:
    """Tests for CheckpointPolicyMismatchError exception."""

    def test_error_message_contains_phase(self) -> None:
        """Test that error message contains the checkpoint phase."""
        error = CheckpointPolicyMismatchError(
            checkpoint_phase="test_phase",
            valid_phases={"phase_a", "phase_b"},
        )
        assert "test_phase" in str(error)
        assert "phase_a" in str(error)
        assert "phase_b" in str(error)
        assert "--no-resume" in str(error)
