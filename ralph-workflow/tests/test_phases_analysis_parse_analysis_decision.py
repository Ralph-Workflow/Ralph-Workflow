"""Tests for ralph/phases/analysis.py — analysis decision parsing."""

from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ralph.phases.analysis import parse_analysis_decision_status
from ralph.policy.loader import load_policy


@lru_cache(maxsize=1)
def _default_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as tmp:
        return load_policy(Path(tmp) / ".agent")


@pytest.mark.timeout_seconds(5)
class TestParseAnalysisDecision:
    def _default_pipeline_policy(self) -> object:
        return _default_policy_bundle().pipeline

    def _make_context(self, workspace: MagicMock) -> MagicMock:
        ctx = MagicMock()
        ctx.workspace = workspace
        ctx.artifacts_policy = MagicMock()
        ctx.artifacts_policy.artifacts = {}
        ctx.pipeline_policy = self._default_pipeline_policy()
        return ctx

    def test_missing_artifact_defaults_to_failure(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = False
        ctx = self._make_context(workspace)

        result = parse_analysis_decision_status(ctx, "development_analysis")
        assert result is None

    def test_completed_status_maps_to_proceed(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.return_value = (
            '{"type":"development_analysis_decision","content":{"status":"completed"}}'
        )
        ctx = self._make_context(workspace)

        result = parse_analysis_decision_status(ctx, "development_analysis")
        assert result == "completed"

    def test_request_changes_status_maps_to_revise(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.return_value = (
            '{"type":"review_analysis_decision","content":{"status":"request_changes"}}'
        )
        ctx = self._make_context(workspace)

        result = parse_analysis_decision_status(ctx, "review_analysis")
        assert result == "request_changes"

    def test_failed_status_is_a_valid_decision(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.return_value = (
            '{"type":"development_analysis_decision","content":{"status":"failed"}}'
        )
        ctx = self._make_context(workspace)

        result = parse_analysis_decision_status(ctx, "development_analysis")
        assert result == "failed"

    def test_invalid_synonym_returns_none(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.return_value = (
            '{"type":"development_analysis_decision","content":{"status":"loopback"}}'
        )
        ctx = self._make_context(workspace)

        result = parse_analysis_decision_status(ctx, "development_analysis")
        assert result is None

    def test_unknown_status_returns_none(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.return_value = (
            '{"type":"development_analysis_decision","content":{"status":"escalate"}}'
        )
        ctx = self._make_context(workspace)

        result = parse_analysis_decision_status(ctx, "development_analysis")
        assert result is None

    def test_malformed_json_returns_none(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.return_value = "not valid json"
        ctx = self._make_context(workspace)

        result = parse_analysis_decision_status(ctx, "development_analysis")
        assert result is None

    def test_read_error_returns_none(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.side_effect = RuntimeError("read error")
        ctx = self._make_context(workspace)

        result = parse_analysis_decision_status(ctx, "development_analysis")
        assert result is None

    def test_rejects_invalid_artifact_type_for_drain(self) -> None:
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.return_value = '{"type":"plan","content":{"status":"completed"}}'
        ctx = self._make_context(workspace)

        result = parse_analysis_decision_status(ctx, "development_analysis")

        assert result is None

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

        result = parse_analysis_decision_status(ctx, "review_analysis")

        assert result is None
