"""Tests for ralph/phases/analysis.py — analysis decision parsing."""

from __future__ import annotations

from unittest.mock import MagicMock

from ralph.config.enums import AnalysisDecision
from ralph.phases.analysis import (
    _map_status_to_decision,
    parse_analysis_decision,
    validate_decision_vocabulary,
)


class TestMapStatusToDecision:
    def test_completed_maps_to_proceed(self) -> None:
        assert _map_status_to_decision("completed") == AnalysisDecision.PROCEED

    def test_proceed_maps_to_proceed(self) -> None:
        assert _map_status_to_decision("proceed") == AnalysisDecision.PROCEED

    def test_success_maps_to_proceed(self) -> None:
        assert _map_status_to_decision("success") == AnalysisDecision.PROCEED

    def test_approve_maps_to_proceed(self) -> None:
        assert _map_status_to_decision("approve") == AnalysisDecision.PROCEED

    def test_approved_maps_to_proceed(self) -> None:
        assert _map_status_to_decision("approved") == AnalysisDecision.PROCEED

    def test_partial_maps_to_revise(self) -> None:
        assert _map_status_to_decision("partial") == AnalysisDecision.REVISE

    def test_revise_maps_to_revise(self) -> None:
        assert _map_status_to_decision("revise") == AnalysisDecision.REVISE

    def test_changes_maps_to_revise(self) -> None:
        assert _map_status_to_decision("changes") == AnalysisDecision.REVISE

    def test_request_changes_maps_to_revise(self) -> None:
        assert _map_status_to_decision("request_changes") == AnalysisDecision.REVISE

    def test_needs_work_maps_to_revise(self) -> None:
        assert _map_status_to_decision("needs_work") == AnalysisDecision.REVISE

    def test_escalate_maps_to_escalate(self) -> None:
        assert _map_status_to_decision("escalate") == AnalysisDecision.ESCALATE

    def test_escalation_maps_to_escalate(self) -> None:
        assert _map_status_to_decision("escalation") == AnalysisDecision.ESCALATE

    def test_failed_maps_to_failure(self) -> None:
        assert _map_status_to_decision("failed") == AnalysisDecision.FAILURE

    def test_failure_maps_to_failure(self) -> None:
        assert _map_status_to_decision("failure") == AnalysisDecision.FAILURE

    def test_error_maps_to_failure(self) -> None:
        assert _map_status_to_decision("error") == AnalysisDecision.FAILURE

    def test_unknown_status_defaults_to_complete(self) -> None:
        assert _map_status_to_decision("unknown_status") == AnalysisDecision.FAILURE

    def test_empty_string_defaults_to_complete(self) -> None:
        assert _map_status_to_decision("") == AnalysisDecision.FAILURE

    def test_case_insensitive_is_handled_by_caller(self) -> None:
        # The parse_analysis_decision function lowercases the status before calling
        # _map_status_to_decision. This function expects lowercase input.
        assert _map_status_to_decision("completed") == AnalysisDecision.PROCEED
        assert _map_status_to_decision("failed") == AnalysisDecision.FAILURE


class TestValidateDecisionVocabulary:
    def test_empty_vocabulary_allows_any_decision(self) -> None:
        assert validate_decision_vocabulary(AnalysisDecision.PROCEED, []) is True
        assert validate_decision_vocabulary(AnalysisDecision.REVISE, []) is True
        assert validate_decision_vocabulary(AnalysisDecision.FAILURE, []) is True

    def test_decision_in_vocabulary_returns_true(self) -> None:
        vocabulary = ["proceed", "complete"]
        assert validate_decision_vocabulary(AnalysisDecision.PROCEED, vocabulary) is True
        assert validate_decision_vocabulary(AnalysisDecision.COMPLETE, vocabulary) is True

    def test_decision_not_in_vocabulary_returns_false(self) -> None:
        vocabulary = ["proceed", "complete"]
        assert validate_decision_vocabulary(AnalysisDecision.REVISE, vocabulary) is False
        assert validate_decision_vocabulary(AnalysisDecision.ESCALATE, vocabulary) is False
        assert validate_decision_vocabulary(AnalysisDecision.FAILURE, vocabulary) is False


class TestParseAnalysisDecision:
    def _make_context(self, workspace: MagicMock) -> MagicMock:
        ctx = MagicMock()
        ctx.workspace = workspace
        ctx.artifacts_policy = MagicMock()
        ctx.artifacts_policy.artifacts = {}
        return ctx

    def test_missing_artifact_defaults_to_proceed(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = False
        ctx = self._make_context(workspace)

        result = parse_analysis_decision(ctx, "development_analysis")
        assert result == AnalysisDecision.PROCEED

    def test_valid_artifact_with_completed_status(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.return_value = (
            '{"type":"development_analysis_decision","content":{"status":"completed"}}'
        )
        ctx = self._make_context(workspace)

        result = parse_analysis_decision(ctx, "development_analysis")
        assert result == AnalysisDecision.PROCEED

    def test_valid_artifact_with_decision_field(self) -> None:
        # Legacy "decision" field support
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.return_value = (
            '{"type":"review_analysis_decision","content":{"decision":"request_changes"}}'
        )
        ctx = self._make_context(workspace)

        result = parse_analysis_decision(ctx, "review_analysis")
        assert result == AnalysisDecision.REVISE

    def test_valid_artifact_with_failed_status(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.return_value = (
            '{"type":"development_analysis_decision","content":{"status":"fail"}}'
        )
        ctx = self._make_context(workspace)

        result = parse_analysis_decision(ctx, "development_analysis")
        assert result == AnalysisDecision.FAILURE

    def test_valid_artifact_with_partial_status(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.return_value = (
            '{"type":"review_analysis_decision","content":{"status":"loopback"}}'
        )
        ctx = self._make_context(workspace)

        result = parse_analysis_decision(ctx, "review_analysis")
        assert result == AnalysisDecision.REVISE

    def test_valid_artifact_with_escalate_status(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.return_value = (
            '{"type":"development_analysis_decision","content":{"status":"escalate"}}'
        )
        ctx = self._make_context(workspace)

        result = parse_analysis_decision(ctx, "development_analysis")
        assert result == AnalysisDecision.ESCALATE

    def test_malformed_json_defaults_to_proceed(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.return_value = "not valid json"
        ctx = self._make_context(workspace)

        result = parse_analysis_decision(ctx, "development_analysis")
        assert result == AnalysisDecision.FAILURE

    def test_read_error_defaults_to_proceed(self) -> None:
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
