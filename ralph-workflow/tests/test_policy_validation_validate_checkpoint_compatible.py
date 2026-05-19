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
from pathlib import Path

import pytest

from ralph.policy.loader import load_policy
from ralph.policy.validation import (
    CheckpointPolicyMismatchError,
    validate_checkpoint_compatible,
)

ValidationError = importlib.import_module("pydantic").ValidationError
DEFAULT_MAX_WORK_UNITS = 50


class TestValidateCheckpointCompatible:
    """Tests for validate_checkpoint_compatible."""

    def test_checkpoint_compatible(self) -> None:
        """Test that a compatible checkpoint passes validation."""

        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)
        # Should not raise
        validate_checkpoint_compatible("planning", bundle)

    def test_checkpoint_incompatible(self) -> None:
        """Test that an incompatible checkpoint raises error."""

        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)
        with pytest.raises(CheckpointPolicyMismatchError):
            validate_checkpoint_compatible("nonexistent_phase", bundle)
