"""Restart-resume regression for the conflict-resolution strategy ladder.

A run that is interrupted mid-escalation must resume at the recorded
ladder rung, not at rung zero. Restarting the ladder would re-attempt
the strategies the previous run already proved cannot land, which is
exactly the deadlock the original bug report named.

The test exercises the real persistence path: build a state whose
``conflict_strategy_index`` and ``conflict_strategies_tried`` already
record two failed attempts, persist it via ``ckpt.save``, reload it
via ``ckpt.load``, and feed the loaded state into ``reduce``. The
loaded state -- not the in-memory builder -- is what the reducer
consumes; the index/tried fields must round-trip exactly so the next
failure advances to the next rung rather than restarting.
"""

from __future__ import annotations

from pathlib import Path

from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline.events import PhaseFailureEvent
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy, RecoveryPolicy


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
    """State of a run that already failed two distinct strategies."""
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
    """A restarted run picks up at rung 2, never restarts at rung 0.

    The pre-restart state records two distinct strategies as already
    tried. After persistence round-trip, the next ``reduce`` call must
    record rung 3 (``merge_instead``), not rung 0.
    """
    checkpoint = tmp_path / "checkpoint.json"
    prior = _interrupted_state()

    # Real persistence round-trip -- not in-memory hand-off.
    ckpt.save(prior, checkpoint)
    loaded = ckpt.load(checkpoint)

    assert loaded is not None
    # Round-trip preserves the recorded ladder position.
    assert loaded.rebase.conflict_strategy_index == 2
    assert loaded.rebase.conflict_strategies_tried == prior.rebase.conflict_strategies_tried

    # The loaded state -- not the builder -- is the reducer's input.
    reason = "integration conflict requires resolution: a.py"
    next_state, _ = reduce(
        loaded,
        PhaseFailureEvent(phase="development", reason=reason, recoverable=False),
        _policy(),
    )

    # The ladder advanced by exactly one rung, never restarted.
    assert next_state.rebase.conflict_strategy_index == 3
    assert next_state.rebase.resolution_exhausted is False
    assert [entry.split(":", 1)[0] for entry in next_state.rebase.conflict_strategies_tried] == [
        "rebase_resolver",
        "refresh_retry",
        "merge_instead",
    ]
    # The freshly recorded rung names the strategy it produced.
    assert next_state.rebase.conflict_strategies_tried[-1].startswith("merge_instead:")


def test_strategy_escalation_resume_persists_the_new_rung(tmp_path: Path) -> None:
    """The rung recorded after a restart round-trips again on the next load."""
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
        ),
        _policy(),
    )

    # Persist the resumed run's new state and reload to prove the
    # recovery record survives the second interruption.
    ckpt.save(next_state, checkpoint)
    reloaded = ckpt.load(checkpoint)

    assert reloaded is not None
    assert reloaded.rebase.conflict_strategy_index == 3
    assert [entry.split(":", 1)[0] for entry in reloaded.rebase.conflict_strategies_tried] == [
        "rebase_resolver",
        "refresh_retry",
        "merge_instead",
    ]
