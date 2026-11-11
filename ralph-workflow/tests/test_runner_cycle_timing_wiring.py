from __future__ import annotations

from functools import lru_cache
from itertools import count
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from ralph.config.enums import Verbosity
from ralph.display.context import make_display_context
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.policy.models import PolicyBundle

_CONSUMED = 3600.0
_STEP_SECONDS = 5.0
_CLOCK_NOW = 1000.0


@lru_cache(maxsize=1)
def _bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


_ = _bundle()


class _Captured:
    def __init__(self) -> None:
        self.materialize_kwargs: dict[str, object] = {}
        self.reduce_kwargs: list[object] = []
        self.folded: list[float] = []


def _drive_step(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    *,
    state: PipelineState | None = None,
    sample_box: list[float | None] | None = None,
) -> _Captured:
    captured = _Captured()
    chaining = state is not None
    state = state or PipelineState(
        phase="development",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=_CONSUMED,
    )
    effect = InvokeAgentEffect(
        agent_name="claude",
        phase="development",
        prompt_file="PROMPT.md",
        drain="development",
        chain_name="development",
    )
    monkeypatch.setattr(
        runner_module, "call_determine_effect_from_policy", lambda *_a, **_k: effect
    )

    def _materialize(*_args: object, **kwargs: object) -> None:
        captured.materialize_kwargs.update(kwargs)

    def _reduce(
        current_state: PipelineState,
        _event: object,
        _policy: object,
        recovery: object = None,
        routing_timing: object = None,
    ) -> tuple[PipelineState, list[object]]:
        captured.reduce_kwargs.append(routing_timing)
        return current_state, []

    if sample_box is not None:
        box = sample_box
    else:
        monkeypatch.setattr(runner_module.time, "monotonic", lambda: _CLOCK_NOW)
        box = [_CLOCK_NOW - _STEP_SECONDS]

    real_fold = runner_module.fold_cycle_elapsed

    def _fold(
        before: PipelineState,
        after: PipelineState,
        *,
        delta_seconds: float,
        timing_enabled: bool,
    ) -> PipelineState:
        captured.folded.append(delta_seconds)
        return real_fold(before, after, delta_seconds=delta_seconds, timing_enabled=timing_enabled)

    monkeypatch.setattr(runner_module, "materialize_agent_prompt_if_needed", _materialize)
    monkeypatch.setattr(
        runner_module,
        "invoke_execute_effect_with_optional_display",
        lambda *_a, **_k: PipelineEvent.AGENT_SUCCESS,
    )
    monkeypatch.setattr(
        runner_module, "phase_event_after_agent_run", lambda **_k: PipelineEvent.AGENT_SUCCESS
    )
    monkeypatch.setattr(runner_module, "reducer_reduce", _reduce)
    monkeypatch.setattr(runner_module, "fold_cycle_elapsed", _fold)
    monkeypatch.setattr(runner_module.ckpt, "save", lambda *_a, **_k: None)

    display_context = make_display_context()
    registry = MagicMock()
    registry.get.return_value = None
    stepped = runner_module.run_pipeline_step(
        state=state,
        policy_bundle=_bundle(),
        workspace_scope=WorkspaceScope(tmp_path),
        config=MagicMock(),
        display=runner_module.ParallelDisplay(display_context),
        display_context=display_context,
        verbosity=Verbosity.QUIET,
        registry=registry,
        pipeline_subscriber=None,
        _cycle_sample_box=box,
    )
    return stepped if chaining else captured


def test_the_step_hands_prompt_materialization_the_cycles_elapsed_total(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    captured = _drive_step(monkeypatch, tmp_path)

    assert captured.materialize_kwargs["cycle_total_elapsed"] == pytest.approx(
        _CONSUMED + _STEP_SECONDS, abs=2.0
    )


def test_the_step_hands_the_reducer_its_routing_timing(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    captured = _drive_step(monkeypatch, tmp_path)

    assert captured.reduce_kwargs
    assert all(timing is not None for timing in captured.reduce_kwargs)
    assert captured.reduce_kwargs[0].total_elapsed_seconds == pytest.approx(
        _CONSUMED + _STEP_SECONDS, abs=2.0
    )


def test_the_step_folds_its_elapsed_time_into_the_cycle(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    captured = _drive_step(monkeypatch, tmp_path)

    assert captured.folded
    assert captured.folded[0] >= _STEP_SECONDS


def test_consumed_time_accumulates_across_consecutive_steps(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    box: list[float | None] = [None]
    state = PipelineState(
        phase="development",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=0.0,
    )
    clock = count(100.0, 10.0)
    monkeypatch.setattr(runner_module.time, "monotonic", lambda: next(clock))

    consumed: list[float] = []
    for _ in range(3):
        state = _drive_step(monkeypatch, tmp_path, state=state, sample_box=box)
        consumed.append(state.cycle_timebox_consumed_seconds)

    assert consumed[0] > 0.0
    assert consumed[1] > consumed[0]
    assert consumed[2] > consumed[1]


def test_the_step_hands_the_inline_effect_path_its_routing_timing(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    seen: list[object] = []
    monkeypatch.setattr(
        runner_module,
        "handle_inline_effect",
        lambda **kwargs: seen.append(kwargs.get("routing_timing")) or kwargs["state"],
    )
    monkeypatch.setattr(
        runner_module,
        "call_determine_effect_from_policy",
        lambda *_a, **_k: runner_module.SaveCheckpointEffect(),
    )
    monkeypatch.setattr(runner_module.ckpt, "save", lambda *_a, **_k: None)

    display_context = make_display_context()
    registry = MagicMock()
    registry.get.return_value = None
    monkeypatch.setattr(runner_module.time, "monotonic", lambda: _CLOCK_NOW)
    runner_module.run_pipeline_step(
        state=PipelineState(
            phase="development",
            cycle_timebox_active=True,
            cycle_timebox_consumed_seconds=_CONSUMED,
        ),
        policy_bundle=_bundle(),
        workspace_scope=WorkspaceScope(tmp_path),
        config=MagicMock(),
        display=runner_module.ParallelDisplay(display_context),
        display_context=display_context,
        verbosity=Verbosity.QUIET,
        registry=registry,
        pipeline_subscriber=None,
        _cycle_sample_box=[_CLOCK_NOW - _STEP_SECONDS],
    )

    assert seen
    assert seen[0] is not None
    assert seen[0].total_elapsed_seconds == pytest.approx(_CONSUMED + _STEP_SECONDS, abs=2.0)
