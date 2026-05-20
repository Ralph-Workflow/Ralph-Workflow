"""Tests for read_product_spec_artifact."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.product_spec import read_product_spec_artifact

if TYPE_CHECKING:
    from pathlib import Path


class TestReadProductSpecArtifact:
    """Tests for read_product_spec_artifact."""

    def test_read_returns_none_when_no_artifact(self, tmp_path: Path) -> None:
        """Reader returns None when no artifact file exists."""
        result = read_product_spec_artifact(tmp_path)
        assert result is None
