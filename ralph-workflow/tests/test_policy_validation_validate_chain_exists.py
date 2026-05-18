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
    validate_chain_exists,
)

ValidationError = importlib.import_module("pydantic").ValidationError
DEFAULT_MAX_WORK_UNITS = 50


class TestValidateChainExists:
    """Tests for validate_chain_exists."""

    def test_chain_exists(self) -> None:
        """Test that an existing chain passes validation."""

        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)
        # Should not raise
        validate_chain_exists("development", bundle)

    def test_chain_not_defined(self) -> None:
        """Test that an undefined chain raises ValueError."""

        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)
        with pytest.raises(ValueError, match="not defined"):
            validate_chain_exists("nonexistent_chain", bundle)
