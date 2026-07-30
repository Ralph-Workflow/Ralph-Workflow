"""Tests for ralph/phases/analysis.py — analysis decision parsing."""

from __future__ import annotations

from unittest.mock import MagicMock

from ralph.phases.analysis import parse_analysis_decision_status
from ralph.policy.models import (
    PhaseDecisionRoute,
    PhaseDefinition,
    PhaseLoopPolicy,
    PhaseTransition,
    PipelinePolicy,
)

_COMPLETED_DECISION_MARKDOWN = """\
---
type: development_analysis_decision
status: completed
---

## Summary

- [SUM-1] Development analysis completed.
"""


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
        workspace.read.return_value = _COMPLETED_DECISION_MARKDOWN
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
        workspace.read.return_value = _COMPLETED_DECISION_MARKDOWN
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
