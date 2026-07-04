"""Tests for MissingPlanHandoffError recovery from any non-planning phase.

When a non-planning phase requires a plan handoff at .agent/PLAN.md and the
handoff is missing, ``prepare_prompt`` raises ``MissingPlanHandoffError``.
The runner must catch that exception and re-route the pipeline back to
``pipeline_policy.entry_phase`` (compiled at policy load time to ``"planning"``
for the default policy) for ANY phase that triggers it, not only
``failed_route``.

Mirrors the construction pattern from
``tests/test_handle_inline_effect_phase_entry_clearing.py``: same imports,
same ``load_policy`` + ``_load_default_policy_bundle`` helper, same
``runner_module.handle_inline_effect`` invocation. The test uses
``MemoryWorkspace`` so the runner's ``ckpt.save`` writes only to the
in-process storage, and a ``tmp_path``-rooted ``checkpoint_path`` so the
real on-disk ``checkpoint.json`` is never touched.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import PreparePromptEffect
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.policy.models import ArtifactsPolicy, PipelinePolicy


def _load_default_policy_bundle() -> tuple[PipelinePolicy, ArtifactsPolicy]:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    bundle = load_policy(defaults_dir)
    return bundle.pipeline, bundle.artifacts


class TestRunnerMissingPlanHandoffRecovery:
    """MissingPlanHandoffError from any non-planning phase routes to entry phase."""

    @pytest.mark.timeout_seconds(15)
    def test_handle_inline_effect_recovers_missing_plan_handoff_from_development_phase(
        self, tmp_path: Path
    ) -> None:
        """Development phase with no .agent/PLAN.md re-routes to planning.

        Asserts:
            - return value is a PipelineState (not an int / exception)
            - recovered_state.phase == PipelinePhase.PLANNING
            - recovered_state.recovery_epoch == 1
            - recovered_state.last_error contains "MissingPlanHandoffError"
        """
        pipeline_policy, artifacts_policy = _load_default_policy_bundle()

        # The pre-compiled entry_phase must be "planning" for the default policy.
        assert pipeline_policy.entry_phase == "planning", (
            "Default pipeline must compile entry_phase='planning' "
            "(defaults/pipeline.toml entry_block='developer_iteration' → first child phase)."
        )

        root = tmp_path
        workspace_scope = WorkspaceScope(
            root=root, allowed_roots=frozenset([root])
        )

        # No plan handoff exists at .agent/PLAN.md inside MemoryWorkspace.
        effect = PreparePromptEffect(
            phase="development",
        )
        state = PipelineState(
            phase="development",
            previous_phase=None,
            checkpoint_saved_count=0,
            recovery_epoch=0,
        )

        result = runner_module.handle_inline_effect(
            effect=effect,
            state=state,
            pipeline_policy=pipeline_policy,
            artifacts_policy=artifacts_policy,
            agents_policy=None,
            registry=None,
            config=None,
            display=None,
            workspace_scope=workspace_scope,
        )

        # (a) the return value is a PipelineState
        assert isinstance(result, PipelineState), (
            f"handle_inline_effect must return a PipelineState, got {type(result).__name__}"
        )

        # (b) state.phase advances to planning (entry_phase)
        assert result.phase == "planning", (
            f"Recovered phase must be planning (entry_phase), got {result.phase}"
        )

        # (c) recovery_epoch increments by 1
        assert result.recovery_epoch == 1, (
            f"recovery_epoch must be 1 after one recovery, got {result.recovery_epoch}"
        )

        # (d) last_error captures the underlying MissingPlanHandoffError message
        assert "plan handoff" in (result.last_error or ""), (
            f"last_error must describe the plan handoff failure, got {result.last_error!r}"
        )
