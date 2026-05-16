"""Tests for ralph/phases/analysis.py — analysis decision parsing."""

from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ralph.phases import HANDLERS, PhaseHandlerNotFoundError, handle_phase, register_role_handlers
from ralph.phases.analysis import handle_generic_analysis_phase, parse_analysis_decision_status
from ralph.phases.artifacts import decision_vocabulary_for_drain
from ralph.phases.commit import handle_commit_phase
from ralph.phases.execution import handle_execution_phase
from ralph.phases.review import handle_review
from ralph.pipeline.effects import InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import PhaseFailureEvent
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    PhaseCommitPolicy,
    PhaseDecisionRoute,
    PhaseDefinition,
    PhaseLoopPolicy,
    PhaseTransition,
    PipelinePolicy,
)


@lru_cache(maxsize=1)
def _default_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as tmp:
        return load_policy(Path(tmp) / ".agent")


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


def _load_default_pipeline_policy() -> object:
    with tempfile.TemporaryDirectory() as tmp:
        return load_policy(Path(tmp) / ".agent").pipeline


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
            workspace.read.return_value = (
                f'{{"type":"development_analysis_decision",'
                f'"content":{{"status":"{status}","summary":"test"}}}}'
            )
            ctx = MagicMock()
            ctx.workspace = workspace
            ctx.artifacts_policy = MagicMock()
            ctx.artifacts_policy.artifacts = {}
            ctx.pipeline_policy = _load_default_pipeline_policy()
            result = parse_analysis_decision_status(ctx, "development_analysis")
            assert result is not None, (
                f"Vocabulary entry '{status}' for development_analysis "
                "must parse to a non-None status"
            )


class TestParseAnalysisDecisionPhaseNameParameter:
    """parse_analysis_decision uses phase_name for policy lookup, drain_name for artifact path."""

    def _make_custom_analysis_policy(self) -> object:
        return PipelinePolicy(
            phases={
                "custom_analysis": PhaseDefinition(
                    drain="development_analysis",
                    role="analysis",
                    transitions=PhaseTransition(
                        on_success="development_commit",
                        on_loopback="development",
                        on_failure=None,
                    ),
                    loop_policy=PhaseLoopPolicy(
                        iteration_state_field="development_analysis_iteration"
                    ),
                    decisions={
                        "completed": PhaseDecisionRoute(
                            target="development_commit", reset_loop=True
                        ),
                        "request_changes": PhaseDecisionRoute(
                            target="development", reset_loop=False
                        ),
                        "failed": PhaseDecisionRoute(target="failed_terminal", reset_loop=False),
                    },
                ),
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                ),
                "development": PhaseDefinition(
                    drain="development",
                    role="execution",
                    transitions=PhaseTransition(
                        on_success="custom_analysis",
                        on_failure=None,
                    ),
                ),
                "failed_terminal": PhaseDefinition(
                    drain="development",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="failed_terminal"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="development",
            terminal_phase="complete",
        )

    def test_phase_name_parameter_used_for_policy_lookup(self) -> None:
        """When phase_name is provided, it is used for decisions table lookup in policy."""
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.return_value = (
            '{"type":"development_analysis_decision","content":{"status":"completed"}}'
        )
        ctx = MagicMock()
        ctx.workspace = workspace
        ctx.artifacts_policy = MagicMock()
        ctx.artifacts_policy.artifacts = {}
        ctx.pipeline_policy = self._make_custom_analysis_policy()

        result = parse_analysis_decision_status(
            ctx, "development_analysis", phase_name="custom_analysis"
        )
        assert result == "completed"

    def test_without_phase_name_uses_drain_name_for_policy_lookup(self) -> None:
        """Without phase_name, drain_name falls back — returns status when phase not in policy."""
        workspace = MagicMock()
        workspace.exists.return_value = True
        workspace.read.return_value = (
            '{"type":"development_analysis_decision","content":{"status":"completed"}}'
        )
        ctx = MagicMock()
        ctx.workspace = workspace
        ctx.artifacts_policy = MagicMock()
        ctx.artifacts_policy.artifacts = {}
        ctx.pipeline_policy = self._make_custom_analysis_policy()

        # drain_name="development_analysis" is NOT a phase name in this custom policy
        # (phases are: custom_analysis, development_commit, development, complete).
        # When phase_def is None, the policy decisions check is skipped and the
        # raw status is returned as-is.
        result = parse_analysis_decision_status(ctx, "development_analysis")
        assert result == "completed"


class TestRegisterRoleHandlers:
    """register_role_handlers wires generic handlers for analysis- and commit-role phases."""

    def _make_analysis_policy(self) -> PipelinePolicy:
        return PipelinePolicy(
            phases={
                "my_custom_analysis": PhaseDefinition(
                    drain="development_analysis",
                    role="analysis",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                        on_failure=None,
                    ),
                    loop_policy=PhaseLoopPolicy(
                        iteration_state_field="development_analysis_iteration"
                    ),
                    decisions={
                        "completed": PhaseDecisionRoute(target="complete", reset_loop=True),
                    },
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="my_custom_analysis",
            terminal_phase="complete",
        )

    def _make_commit_policy(self) -> PipelinePolicy:
        return PipelinePolicy(
            phases={
                "my_custom_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="iteration",
                        loop_resets=["development_analysis_iteration"],
                    ),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="my_custom_commit",
            terminal_phase="complete",
        )

    def test_register_role_handlers_registers_analysis_phase(self) -> None:
        """register_role_handlers adds a generic handler for analysis-role phases."""
        policy = self._make_analysis_policy()
        HANDLERS.pop("my_custom_analysis", None)

        assert "my_custom_analysis" not in HANDLERS
        register_role_handlers(policy)
        assert HANDLERS.get("my_custom_analysis") is handle_generic_analysis_phase
        HANDLERS.pop("my_custom_analysis", None)

    def test_register_role_handlers_registers_commit_phase(self) -> None:
        """register_role_handlers adds handle_commit_phase for commit-role phases."""
        policy = self._make_commit_policy()
        HANDLERS.pop("my_custom_commit", None)

        assert "my_custom_commit" not in HANDLERS
        register_role_handlers(policy)
        assert HANDLERS.get("my_custom_commit") is handle_commit_phase
        HANDLERS.pop("my_custom_commit", None)

    def test_register_role_handlers_does_not_overwrite_existing_handler(self) -> None:
        """register_role_handlers skips phases that already have a handler registered."""
        policy = self._make_analysis_policy()
        # Pre-register the generic handler; a second call must not overwrite it.
        HANDLERS["my_custom_analysis"] = handle_generic_analysis_phase
        try:
            register_role_handlers(policy)
            assert HANDLERS["my_custom_analysis"] is handle_generic_analysis_phase
        finally:
            del HANDLERS["my_custom_analysis"]

    def test_handle_phase_dispatches_to_registered_analysis_handler(self) -> None:
        """After register_role_handlers, handle_phase dispatches a custom analysis phase."""
        policy = self._make_analysis_policy()
        HANDLERS.pop("my_custom_analysis", None)
        register_role_handlers(policy)

        workspace = MagicMock()
        workspace.exists.return_value = False
        ctx = MagicMock()
        ctx.workspace = workspace
        ctx.pipeline_policy = policy
        ctx.artifacts_policy = MagicMock()
        ctx.artifacts_policy.artifacts = {}

        effect = InvokeAgentEffect(
            agent_name="test-agent",
            phase="my_custom_analysis",
            prompt_file="/tmp/prompt.md",
            drain="development_analysis",
        )
        try:
            events = handle_phase(effect, ctx)
            assert len(events) == 1
            assert isinstance(events[0], PhaseFailureEvent)
        finally:
            HANDLERS.pop("my_custom_analysis", None)

    def test_handle_phase_dispatches_to_registered_commit_handler(self) -> None:
        """After register_role_handlers, handle_phase dispatches a custom commit phase."""
        policy = self._make_commit_policy()
        HANDLERS.pop("my_custom_commit", None)
        register_role_handlers(policy)

        ctx = MagicMock()

        # PreparePromptEffect returns [] from handle_commit_phase (non-InvokeAgentEffect path)
        effect = PreparePromptEffect(phase="my_custom_commit", iteration=1)
        try:
            events = handle_phase(effect, ctx)
            assert events == []
        finally:
            HANDLERS.pop("my_custom_commit", None)

    def test_register_role_handlers_registers_execution_phase(self) -> None:
        """register_role_handlers adds handle_execution_phase for execution-role phases."""
        policy = PipelinePolicy(
            phases={
                "my_custom_build": PhaseDefinition(
                    drain="my_custom_build",
                    role="execution",
                    transitions=PhaseTransition(on_success="done"),
                ),
                "done": PhaseDefinition(
                    drain="done",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(on_success="done", on_loopback="done"),
                ),
            },
            entry_phase="my_custom_build",
            terminal_phase="done",
        )
        HANDLERS.pop("my_custom_build", None)
        register_role_handlers(policy)
        assert HANDLERS.get("my_custom_build") is handle_execution_phase
        HANDLERS.pop("my_custom_build", None)

    def test_register_role_handlers_registers_review_phase(self) -> None:
        """register_role_handlers adds handle_review for review-role phases."""
        policy = PipelinePolicy(
            phases={
                "my_custom_audit": PhaseDefinition(
                    drain="my_custom_audit",
                    role="review",
                    transitions=PhaseTransition(on_success="done"),
                ),
                "done": PhaseDefinition(
                    drain="done",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(on_success="done", on_loopback="done"),
                ),
            },
            entry_phase="my_custom_audit",
            terminal_phase="done",
        )
        HANDLERS.pop("my_custom_audit", None)
        register_role_handlers(policy)
        assert HANDLERS.get("my_custom_audit") is handle_review
        HANDLERS.pop("my_custom_audit", None)

    def test_unregistered_phase_raises_handler_not_found(self) -> None:
        """handle_phase raises PhaseHandlerNotFoundError for unregistered phase names."""
        HANDLERS.pop("totally_unknown_phase", None)
        effect = PreparePromptEffect(phase="totally_unknown_phase", iteration=1)
        with pytest.raises(PhaseHandlerNotFoundError) as exc_info:
            handle_phase(effect, MagicMock())
        assert "totally_unknown_phase" in str(exc_info.value)
