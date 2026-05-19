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
from unittest.mock import MagicMock

import pytest

from ralph.cli.commands.init import STARTER_PROMPT_SENTINEL
from ralph.policy.validation import (
    PolicyValidationError,
    validate_required_inputs,
)

if TYPE_CHECKING:
    from pathlib import Path

ValidationError = importlib.import_module("pydantic").ValidationError
DEFAULT_MAX_WORK_UNITS = 50


class TestValidateRequiredInputs:
    """Tests for validate_required_inputs."""

    def test_missing_prompt_md_raises_with_init_hint(self, tmp_path: Path) -> None:
        """Missing PROMPT.md error must mention both the structural prefix and ralph --init."""
        scope = MagicMock()
        scope.root = tmp_path
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_required_inputs(scope)
        msg = str(exc_info.value)
        assert "Required input file not found" in msg
        assert "ralph --init" in msg

    def test_present_prompt_md_does_not_raise(self, tmp_path: Path) -> None:
        """A non-empty PROMPT.md passes validation without error."""
        (tmp_path / "PROMPT.md").write_text("# Goal\n\nDo something.\n")
        scope = MagicMock()
        scope.root = tmp_path
        validate_required_inputs(scope)  # should not raise

    def test_empty_prompt_md_raises(self, tmp_path: Path) -> None:
        """An empty PROMPT.md raises PolicyValidationError."""
        (tmp_path / "PROMPT.md").write_text("")
        scope = MagicMock()
        scope.root = tmp_path
        with pytest.raises(PolicyValidationError, match="empty"):
            validate_required_inputs(scope)

    def test_starter_sentinel_prompt_md_raises(self, tmp_path: Path) -> None:
        """A PROMPT.md with the starter sentinel raises PolicyValidationError."""
        (tmp_path / "PROMPT.md").write_text(
            STARTER_PROMPT_SENTINEL + "\n\n# Goal\n\nExample body\n"
        )
        scope = MagicMock()
        scope.root = tmp_path
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_required_inputs(scope)
        msg = str(exc_info.value)
        assert "starter template" in msg
        assert "ralph" in msg
        assert str(tmp_path) in msg

    def test_edited_prompt_md_passes(self, tmp_path: Path) -> None:
        """A PROMPT.md without the sentinel passes validation."""
        (tmp_path / "PROMPT.md").write_text("# Goal\n\nBuild a real feature here.\n")
        scope = MagicMock()
        scope.root = tmp_path
        validate_required_inputs(scope)  # must not raise

    def test_sentinel_anywhere_in_prompt_raises(self, tmp_path: Path) -> None:
        """Sentinel on any line in PROMPT.md raises PolicyValidationError."""
        (tmp_path / "PROMPT.md").write_text(
            "# Goal\n\nMy task.\n\n" + STARTER_PROMPT_SENTINEL + "\n"
        )
        scope = MagicMock()
        scope.root = tmp_path
        with pytest.raises(PolicyValidationError):
            validate_required_inputs(scope)
