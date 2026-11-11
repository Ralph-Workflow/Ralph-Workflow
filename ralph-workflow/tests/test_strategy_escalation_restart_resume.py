from __future__ import annotations

from pathlib import Path

from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline.events import PhaseFailureEvent
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy, RecoveryPolicy
from ralph.recovery.classifier import FailureCategory


def _policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="complete", on_loopback="development"),
            ),
            "failed_terminal": PhaseDefinition(
                drain="development",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )


def _interrupted_state() -> PipelineState:
    return PipelineState(
        phase="development",
        rebase=RebaseState(
            conflict_strategy_index=2,
            conflict_strategies_tried=(
                "rebase_resolver: integration conflict requires resolution: a.py",
                "refresh_retry: integration conflict requires resolution: a.py",
            ),
        ),
    )


def test_strategy_escalation_resumes_at_recorded_rung_after_restart(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    prior = _interrupted_state()

    ckpt.save(prior, checkpoint)
    loaded = ckpt.load(checkpoint)

    assert loaded is not None
    assert loaded.rebase.conflict_strategy_index == 2
    assert loaded.rebase.conflict_strategies_tried == prior.rebase.conflict_strategies_tried

    reason = "integration conflict requires resolution: a.py"
    next_state, _ = reduce(
        loaded,
        PhaseFailureEvent(
            phase="development",
            reason=reason,
            recoverable=False,
            failure_category=FailureCategory.INTEGRATION,
        ),
        _policy(),
    )

    assert next_state.rebase.conflict_strategy_index == 3
    assert next_state.rebase.resolution_exhausted is False
    assert [entry.split(":", 1)[0] for entry in next_state.rebase.conflict_strategies_tried] == [
        "rebase_resolver",
        "refresh_retry",
        "merge_instead",
    ]
    assert next_state.rebase.conflict_strategies_tried[-1].startswith("merge_instead:")


def test_strategy_escalation_resume_persists_the_new_rung(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    prior = _interrupted_state()

    ckpt.save(prior, checkpoint)
    loaded = ckpt.load(checkpoint)
    assert loaded is not None

    next_state, _ = reduce(
        loaded,
        PhaseFailureEvent(
            phase="development",
            reason="integration conflict requires resolution: a.py",
            recoverable=False,
            failure_category=FailureCategory.INTEGRATION,
        ),
        _policy(),
    )

    ckpt.save(next_state, checkpoint)
    reloaded = ckpt.load(checkpoint)

    assert reloaded is not None
    assert reloaded.rebase.conflict_strategy_index == 3
    assert [entry.split(":", 1)[0] for entry in reloaded.rebase.conflict_strategies_tried] == [
        "rebase_resolver",
        "refresh_retry",
        "merge_instead",
    ]
