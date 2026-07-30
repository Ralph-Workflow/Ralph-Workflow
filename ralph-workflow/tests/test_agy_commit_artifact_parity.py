"""AGY fallback promotion keeps commit artifacts on the canonical path."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.registry import AgentRegistry
from ralph.config.models import UnifiedConfig
from ralph.mcp.artifacts.canonical_submit import promote_fallback_artifact
from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present

if TYPE_CHECKING:
    from pathlib import Path


def _commit_markdown() -> str:
    return """---
type: commit
subject: fix(agy): preserve canonical commit receipt
---

## Body Summary

- [S-1] Preserve canonical commit receipt.
"""


def test_agy_commit_artifact_regression_promotes_valid_fallback_canonically(tmp_path: Path) -> None:
    """DA-004: AGY's commit artifact receives the ordinary canonical receipt."""
    fallback = tmp_path / ".agent" / "tmp" / "commit_message.md"
    fallback.parent.mkdir(parents=True)
    fallback.write_text(_commit_markdown(), encoding="utf-8")

    result = promote_fallback_artifact(tmp_path, "commit_message", run_id="agy-commit-run")

    assert result is not None
    assert result.artifact_path == tmp_path / ".agent" / "artifacts" / "commit_message.md"
    assert artifact_receipt_present(tmp_path, "agy-commit-run", "commit_message")
    config = AgentRegistry.from_config(UnifiedConfig()).get("agy/gemini-3.6-flash-low")
    assert config is not None and config.can_commit is True


def test_agy_commit_artifact_regression_rejects_malformed_fallback(tmp_path: Path) -> None:
    """DA-004: malformed AGY commit artifacts never receive a canonical receipt."""
    fallback = tmp_path / ".agent" / "tmp" / "commit_message.md"
    fallback.parent.mkdir(parents=True)
    fallback.write_text("---\ntype: commit\n---", encoding="utf-8")

    assert promote_fallback_artifact(tmp_path, "commit_message", run_id="agy-commit-run") is None
    assert not artifact_receipt_present(tmp_path, "agy-commit-run", "commit_message")
