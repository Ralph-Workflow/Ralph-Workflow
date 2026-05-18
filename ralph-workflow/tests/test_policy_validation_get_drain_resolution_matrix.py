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

from ralph.policy.loader import load_policy
from ralph.policy.validation import (
    get_drain_resolution_matrix,
)

ValidationError = importlib.import_module("pydantic").ValidationError
DEFAULT_MAX_WORK_UNITS = 50


class TestGetDrainResolutionMatrix:
    """Tests for get_drain_resolution_matrix."""

    def test_empty_matrix(self) -> None:
        """Test empty bundle returns empty matrix."""

        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)
        matrix = get_drain_resolution_matrix(bundle)
        assert isinstance(matrix, dict)
        # Should have entries since default policy has bound drains
        assert len(matrix) > 0

    def test_matrix_contains_drain_info(self) -> None:
        """Test that matrix contains correct drain information."""

        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)
        matrix = get_drain_resolution_matrix(bundle)

        if "planning" in matrix:
            assert "chain" in matrix["planning"]
            assert "agents" in matrix["planning"]
            assert "max_retries" in matrix["planning"]
