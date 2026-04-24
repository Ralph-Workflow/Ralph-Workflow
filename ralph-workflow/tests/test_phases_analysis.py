"""Tests for ralph/phases/analysis.py — analysis decision parsing."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from ralph.config.enums import AnalysisDecision
from ralph.phases.analysis import parse_analysis_decision


class TestParseAnalysisDecision:
    def _make_context(self, workspace: MagicMock) -> MagicMock:
        ctx = MagicMock()
        ctx.workspace = workspace
        ctx.artifacts_policy = MagicMock()
        ctx.artifacts_policy.artifacts = {}
        return ctx

    def test_missing_artifact_defaults_to_failure(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = False
        ctx = self._make_context(workspace)

        result = parse_analysis_decision(ctx, "development_analysis")
        assert result == AnalysisDecision.FAILURE

    def test_completed_status_maps_to_proceed(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.return_value = (
            '{"type":"development_analysis_decision","content":{"status":"completed"}}'
        )
        ctx = self._make_context(workspace)

        result = parse_analysis_decision(ctx, "development_analysis")
        assert result == AnalysisDecision.PROCEED

    def test_request_changes_status_maps_to_revise(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.return_value = (
            '{"type":"review_analysis_decision","content":{"status":"request_changes"}}'
        )
        ctx = self._make_context(workspace)

        result = parse_analysis_decision(ctx, "review_analysis")
        assert result == AnalysisDecision.REVISE

    def test_failed_status_maps_to_failure(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.return_value = (
            '{"type":"development_analysis_decision","content":{"status":"failed"}}'
        )
        ctx = self._make_context(workspace)

        result = parse_analysis_decision(ctx, "development_analysis")
        assert result == AnalysisDecision.FAILURE

    def test_invalid_synonym_defaults_to_failure(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.return_value = (
            '{"type":"review_analysis_decision","content":{"status":"loopback"}}'
        )
        ctx = self._make_context(workspace)

        result = parse_analysis_decision(ctx, "review_analysis")
        assert result == AnalysisDecision.FAILURE

    def test_unknown_status_defaults_to_failure(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.return_value = (
            '{"type":"development_analysis_decision","content":{"status":"escalate"}}'
        )
        ctx = self._make_context(workspace)

        result = parse_analysis_decision(ctx, "development_analysis")
        assert result == AnalysisDecision.FAILURE

    def test_malformed_json_defaults_to_failure(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.return_value = "not valid json"
        ctx = self._make_context(workspace)

        result = parse_analysis_decision(ctx, "development_analysis")
        assert result == AnalysisDecision.FAILURE

    def test_read_error_defaults_to_failure(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.side_effect = RuntimeError("read error")
        ctx = self._make_context(workspace)

        result = parse_analysis_decision(ctx, "development_analysis")
        assert result == AnalysisDecision.FAILURE

    def test_rejects_invalid_artifact_type_for_drain(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.return_value = '{"type":"plan","content":{"status":"completed"}}'
        ctx = self._make_context(workspace)

        result = parse_analysis_decision(ctx, "development_analysis")

        assert result == AnalysisDecision.FAILURE

    def test_rejects_status_not_allowed_by_policy_vocabulary(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.return_value = (
            '{"type":"review_analysis_decision","content":{"status":"approve"}}'
        )
        ctx = self._make_context(workspace)
        contract = MagicMock()
        contract.drain = "review_analysis"
        contract.artifact_type = "review_analysis_decision"
        contract.decision_vocabulary = ["request_changes", "reject", "loopback"]
        ctx.artifacts_policy.artifacts = {"review": contract}

        result = parse_analysis_decision(ctx, "review_analysis")

        assert result == AnalysisDecision.FAILURE


class TestDecisionVocabularyFullCoverage:
    """Every status in the policy decision_vocabulary must map to a concrete AnalysisDecision."""

    def _load_default_policy(self) -> object:
        from ralph.policy.loader import load_policy  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp:
            bundle = load_policy(Path(tmp) / ".agent")
            return bundle.artifacts

    def test_every_development_analysis_vocabulary_entry_maps_to_concrete_decision(
        self,
    ) -> None:
        from ralph.phases.artifacts import decision_vocabulary_for_drain  # noqa: PLC0415

        policy = self._load_default_policy()
        vocab = decision_vocabulary_for_drain(
            policy, "development_analysis", "development_analysis_decision"
        )
        assert vocab, "development_analysis must have a non-empty decision_vocabulary"
        for status in vocab:
            workspace = MagicMock()
            workspace.exists.return_value = True
            workspace.read.return_value = (
                f'{{"type":"development_analysis_decision",'
                f'"content":{{"status":"{status}","summary":"test"}}}}'
            )
            ctx = MagicMock()
            ctx.workspace = workspace
            ctx.artifacts_policy = MagicMock()
            ctx.artifacts_policy.artifacts = {}
            result = parse_analysis_decision(ctx, "development_analysis")
            is_intentional_failure = status in ("failed",)
            assert result != AnalysisDecision.FAILURE or is_intentional_failure, (
                f"Vocabulary entry '{status}' for development_analysis "
                "must not silently map to FAILURE"
            )

    def test_every_review_analysis_vocabulary_entry_maps_to_concrete_decision(self) -> None:
        from ralph.phases.artifacts import decision_vocabulary_for_drain  # noqa: PLC0415

        policy = self._load_default_policy()
        vocab = decision_vocabulary_for_drain(
            policy, "review_analysis", "review_analysis_decision"
        )
        assert vocab, "review_analysis must have a non-empty decision_vocabulary"
        for status in vocab:
            workspace = MagicMock()
            workspace.exists.return_value = True
            workspace.read.return_value = (
                f'{{"type":"review_analysis_decision",'
                f'"content":{{"status":"{status}","summary":"test"}}}}'
            )
            ctx = MagicMock()
            ctx.workspace = workspace
            ctx.artifacts_policy = MagicMock()
            ctx.artifacts_policy.artifacts = {}
            result = parse_analysis_decision(ctx, "review_analysis")
            is_intentional_failure = status in ("failed",)
            assert result != AnalysisDecision.FAILURE or is_intentional_failure, (
                f"Vocabulary entry '{status}' for review_analysis "
                "must not silently map to FAILURE"
            )
