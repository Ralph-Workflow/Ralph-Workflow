"""Fan-out state lifecycle from production-shaped state.

The effect router derives work units from the plan artifact, so the state
entering ``execute_fan_out_sync`` carries EMPTY ``work_units``. The fan-out
runtime must still seed worker tracking (the reducer builds ``worker_states``
from ``state.work_units`` on FAN_OUT_STARTED), advance the phase when every
worker succeeds, clear the wave's tracking state afterwards so downstream
phases route normally, and preserve tracking state on partial failure so a
re-entry resumes only the unfinished units.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline import fan_out
from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.events import PipelineEvent, WorkerCompletedEvent, WorkerFailedEvent
from ralph.pipeline.parallel import coordinator
from ralph.pipeline.state import PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerState, WorkerStatus
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    import pytest

    from ralph.policy.models import PolicyBundle


@lru_cache(maxsize=1)
def _default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _make_work_unit(uid: str) -> WorkUnit:
    return WorkUnit(
        unit_id=uid,
        description=f"Work unit {uid}",
        allowed_directories=[f"src/{uid}"],
    )


def _two_unit_effect() -> FanOutEffect:
    return FanOutEffect(
        work_units=(_make_work_unit("unit-a"), _make_work_unit("unit-b")),
        max_workers=2,
        run_post_fanout_verification=False,
        phase="development",
    )


def _run_fan_out(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    state: PipelineState,
    events: list[object],
    expect_coordinator_call: bool = True,
    checkpointed_states: list[PipelineState] | None = None,
) -> PipelineState:
    coordinator_calls: list[bool] = []

    async def _fake_run_fan_out(**_kwargs: object) -> list[object]:
        coordinator_calls.append(True)
        return events

    def _capture_checkpoint(saved: PipelineState, *_args: object, **_kwargs: object) -> None:
        if checkpointed_states is not None:
            checkpointed_states.append(saved)

    monkeypatch.setattr(coordinator, "run_fan_out", _fake_run_fan_out)
    monkeypatch.setattr(fan_out.ckpt, "save", _capture_checkpoint)

    result = fan_out.execute_fan_out_sync(
        effect=_two_unit_effect(),
        state=state,
        display=ParallelDisplay(make_display_context()),
        policy_bundle=_default_policy_bundle(),
        workspace_scope=WorkspaceScope(tmp_path),
        _install_signal_handlers=lambda *_args, **_kwargs: None,
    )
    assert coordinator_calls == ([True] if expect_coordinator_call else [])
    return result


def test_successful_wave_advances_phase_from_empty_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run_fan_out(
        monkeypatch,
        tmp_path,
        state=PipelineState(phase="development"),
        events=[
            PipelineEvent.FAN_OUT_STARTED,
            WorkerCompletedEvent(unit_id="unit-a", exit_code=0),
            WorkerCompletedEvent(unit_id="unit-b", exit_code=0),
            PipelineEvent.ALL_WORKERS_COMPLETE,
        ],
    )

    assert result.phase == "development_commit_cleanup"


def test_successful_wave_clears_parallel_tracking_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stale units would poison routing of the next (non-parallelized) phase."""
    result = _run_fan_out(
        monkeypatch,
        tmp_path,
        state=PipelineState(phase="development"),
        events=[
            PipelineEvent.FAN_OUT_STARTED,
            WorkerCompletedEvent(unit_id="unit-a", exit_code=0),
            WorkerCompletedEvent(unit_id="unit-b", exit_code=0),
            PipelineEvent.ALL_WORKERS_COMPLETE,
        ],
    )

    assert result.work_units == ()
    assert result.worker_states == {}


def test_partial_failure_preserves_resume_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed wave must keep tracking state so re-entry retries only failures."""
    result = _run_fan_out(
        monkeypatch,
        tmp_path,
        state=PipelineState(phase="development"),
        events=[
            PipelineEvent.FAN_OUT_STARTED,
            WorkerCompletedEvent(unit_id="unit-a", exit_code=0),
            WorkerFailedEvent(unit_id="unit-b", exit_code=1, error="boom"),
        ],
    )

    assert result.phase == "development"
    assert {u.unit_id for u in result.work_units} == {"unit-a", "unit-b"}
    assert result.worker_states["unit-a"].status == WorkerStatus.SUCCEEDED
    assert result.worker_states["unit-b"].status == WorkerStatus.FAILED


def test_successful_wave_checkpoints_cleared_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash after the wave must not leave a checkpoint that poisons resume.

    The last checkpoint written by a fully successful wave must already have
    the parallel tracking cleared; otherwise resume routes the advanced
    (non-parallelized) phase with stale work_units and exits with a hard
    failure instead of continuing the run.
    """
    checkpointed: list[PipelineState] = []
    _run_fan_out(
        monkeypatch,
        tmp_path,
        state=PipelineState(phase="development"),
        events=[
            PipelineEvent.FAN_OUT_STARTED,
            WorkerCompletedEvent(unit_id="unit-a", exit_code=0),
            WorkerCompletedEvent(unit_id="unit-b", exit_code=0),
            PipelineEvent.ALL_WORKERS_COMPLETE,
        ],
        checkpointed_states=checkpointed,
    )

    assert checkpointed, "fan-out must checkpoint after the wave"
    last = checkpointed[-1]
    assert last.work_units == (), "checkpointed state must not retain work_units"
    assert last.worker_states == {}, "checkpointed state must not retain worker tracking"


def test_failed_wave_checkpoints_resume_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed wave's checkpoint must keep tracking state so resume can retry."""
    checkpointed: list[PipelineState] = []
    _run_fan_out(
        monkeypatch,
        tmp_path,
        state=PipelineState(phase="development"),
        events=[
            PipelineEvent.FAN_OUT_STARTED,
            WorkerCompletedEvent(unit_id="unit-a", exit_code=0),
            WorkerFailedEvent(unit_id="unit-b", exit_code=1, error="boom"),
        ],
        checkpointed_states=checkpointed,
    )

    assert checkpointed, "fan-out must checkpoint after the wave"
    last = checkpointed[-1]
    assert {u.unit_id for u in last.work_units} == {"unit-a", "unit-b"}
    assert last.worker_states["unit-a"].status == WorkerStatus.SUCCEEDED
    assert last.worker_states["unit-b"].status == WorkerStatus.FAILED


def test_resume_with_all_units_succeeded_advances_instead_of_stalling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Crash-resume after the wave finished must advance, not loop forever."""
    units = _two_unit_effect().work_units
    state = PipelineState(
        phase="development",
        work_units=units,
        worker_states={
            "unit-a": WorkerState(unit_id="unit-a", status=WorkerStatus.SUCCEEDED),
            "unit-b": WorkerState(unit_id="unit-b", status=WorkerStatus.SUCCEEDED),
        },
    )

    result = _run_fan_out(
        monkeypatch,
        tmp_path,
        state=state,
        events=[],
        expect_coordinator_call=False,
    )

    assert result.phase == "development_commit_cleanup"
    assert result.work_units == ()
    assert result.worker_states == {}
