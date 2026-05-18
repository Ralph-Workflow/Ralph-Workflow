"""Tests for ralph/phases/analysis.py — analysis decision parsing."""

from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ralph.phases import HANDLERS, PhaseHandlerNotFoundError, handle_phase, register_role_handlers
from ralph.phases.analysis import handle_generic_analysis_phase
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
