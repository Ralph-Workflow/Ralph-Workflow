"""Tests for ralph/phases/analysis.py — analysis decision parsing."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from ralph.phases.analysis import parse_analysis_decision_status
from ralph.phases.artifacts import decision_vocabulary_for_drain
from ralph.policy.loader import load_policy


def _load_default_pipeline_policy() -> object:
    with tempfile.TemporaryDirectory() as tmp:
        bundle = load_policy(Path(tmp) / ".agent")
        return bundle.pipeline


def _development_analysis_markdown(status: str) -> str:
    feedback = (
        "\n## What Came Up Short\n\n"
        "- [W-1] The implementation needs another development pass.\n"
        "\n## How To Fix\n\n"
        "- [FIX-1] Address the remaining implementation gap.\n"
        if status in {"request_changes", "failed"}
        else ""
    )
    return (
        "---\n"
        "type: development_analysis_decision\n"
        f"status: {status}\n"
        "---\n"
        "\n## Summary\n\n"
        "- [SUM-1] Development analysis reached a policy-defined decision.\n"
        f"{feedback}"
    )


class TestDecisionVocabularyFullCoverage:
    """Every status in the policy decision_vocabulary must be parseable (return non-None)."""

    def _load_default_policy(self) -> object:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = load_policy(Path(tmp) / ".agent")
            return bundle.artifacts

    def test_every_development_analysis_vocabulary_entry_is_parseable(
        self,
    ) -> None:
        policy = self._load_default_policy()
        vocab = decision_vocabulary_for_drain(
            policy, "development_analysis", "development_analysis_decision"
        )
        assert vocab, "development_analysis must have a non-empty decision_vocabulary"
        for status in vocab:
            workspace = MagicMock()
            workspace.exists.return_value = True
            workspace.read.return_value = _development_analysis_markdown(status)
            ctx = MagicMock()
            ctx.workspace = workspace
            ctx.artifacts_policy = MagicMock()
            ctx.artifacts_policy.artifacts = {}
            ctx.pipeline_policy = _load_default_pipeline_policy()
            result = parse_analysis_decision_status(ctx, "development_analysis")
            assert result == status, (
                f"Vocabulary entry '{status}' for development_analysis "
                "must round-trip through the canonical Markdown artifact"
            )
