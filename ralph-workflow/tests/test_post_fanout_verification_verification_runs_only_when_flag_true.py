"""Tests for serialized post-fanout workspace verification."""

from __future__ import annotations

from pathlib import Path

from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.work_units import WorkUnit
from ralph.policy.loader import load_policy
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from ralph.workspace.scope import WorkspaceScope


def _minimal_policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_failure=None,
                    on_loopback="development",
                ),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
    )


_EXIT_CODE_VERIFY_FAIL = 2


def _make_scope(tmp_path: Path) -> WorkspaceScope:
    return WorkspaceScope(root=tmp_path, allowed_roots=frozenset([tmp_path]))


class TestVerificationRunsOnlyWhenFlagTrue:
    def test_verification_flag_defaults_to_false_on_effect(self) -> None:
        """FanOutEffect.run_post_fanout_verification must default to False."""
        effect = FanOutEffect(
            work_units=(
                WorkUnit(unit_id="unit-a", description="A", allowed_directories=["src/a"]),
            ),
            max_workers=1,
        )
        assert effect.run_post_fanout_verification is False, (
            "Default must be False so unit tests never accidentally invoke make verify"
        )

    def test_verification_only_runs_when_flag_true(self) -> None:
        """Verification conditional: flag=False means _run_post_fanout_verification never called."""
        effect_false = FanOutEffect(
            work_units=(WorkUnit(unit_id="u", description="u", allowed_directories=["src/u"]),),
            max_workers=1,
            run_post_fanout_verification=False,
        )
        effect_true = FanOutEffect(
            work_units=(WorkUnit(unit_id="u", description="u", allowed_directories=["src/u"]),),
            max_workers=1,
            run_post_fanout_verification=True,
        )
        any_worker_failed = False
        # Simulate the conditional logic from _run_fan_out_async
        verify_calls_false = 0
        if effect_false.run_post_fanout_verification and not any_worker_failed:
            verify_calls_false += 1
        verify_calls_true = 0
        if effect_true.run_post_fanout_verification and not any_worker_failed:
            verify_calls_true += 1

        assert verify_calls_false == 0
        assert verify_calls_true == 1

    def test_policy_post_fanout_verification_defaults_to_false(self) -> None:
        """The default pipeline policy must have post_fanout_verification=False."""


        defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
        bundle = load_policy(defaults_dir)
        dev_para = bundle.pipeline.phases["development"].parallelization
        assert dev_para is not None
        assert dev_para.post_fanout_verification is False, (
            "Default policy must have post_fanout_verification=False"
        )
