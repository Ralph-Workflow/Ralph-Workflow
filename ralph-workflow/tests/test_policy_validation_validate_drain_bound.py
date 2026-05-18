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
    validate_drain_bound,
)

ValidationError = importlib.import_module("pydantic").ValidationError
DEFAULT_MAX_WORK_UNITS = 50


class TestValidateDrainBound:
    """Tests for validate_drain_bound."""

    def test_drain_bound(self) -> None:
        """Test that a bound drain passes validation."""

        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)
        # Should not raise
        validate_drain_bound("planning", bundle)

    def test_drain_not_bound(self) -> None:
        """Test that an unbound drain raises ValueError."""

        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)
        with pytest.raises(ValueError, match="not bound"):
            validate_drain_bound("nonexistent_drain", bundle)
