"""The runner's three connections to the cycle timebox must actually be made.

Each of these could be severed — the fold deleted, the routing timing passed as
None, the elapsed total not handed to prompt materialization — with the whole
suite green, because the surrounding tests drive the pure helpers directly or
re-implement the runner's orchestration. Severing any one of them disables the
feature in production: without the fold no time accumulates, without the timing
no deadline is enforced, and without the elapsed total nothing is published, so
the agent is never warned and the artifact gate can never fire.

These assert on the arguments the runner hands its real collaborators, which is
what a mutation to the call site changes.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

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


@lru_cache(maxsize=1)
def _bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


# Warm the policy parse at collection time; these tests share the repo's
# per-test timeout and a cold TOML parse alone can exhaust it under xdist.
_ = _bundle()


class _Captured:
    def __init__(self) -> None:
        self.materialize_kwargs: dict[str, object] = {}
        self.reduce_kwargs: list[object] = []
        self.folded: list[float] = []


def _drive_step(monkeypatch: MonkeyPatch, tmp_path: Path) -> _Captured:
    """Run one real pipeline step inside a guarded cycle, observing the seams."""
    captured = _Captured()
    state = PipelineState(
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

    real_fold = runner_module.fold_cycle_elapsed

    def _fold(
        before: PipelineState,
        after: PipelineState,
        *,
        delta_seconds: float,
        timing_enabled: bool,
    ) -> PipelineState:
        captured.folded.append(delta_seconds)
        return real_fold(
            before, after, delta_seconds=delta_seconds, timing_enabled=timing_enabled
        )

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
    runner_module.run_pipeline_step(
        state=state,
        policy_bundle=_bundle(),
        workspace_scope=WorkspaceScope(tmp_path),
        config=MagicMock(),
        display=runner_module.ParallelDisplay(display_context),
        display_context=display_context,
        verbosity=Verbosity.QUIET,
        registry=registry,
        pipeline_subscriber=None,
        # The run loop owns this sample box; without it the runner has no
        # monotonic anchor and every timing seam below goes silently null.
        _cycle_sample_box=[None],
    )
    return captured


def test_the_step_hands_prompt_materialization_the_cycles_elapsed_total(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Without it nothing is published: no prompt warning, no nag, no gate."""
    captured = _drive_step(monkeypatch, tmp_path)

    assert captured.materialize_kwargs["cycle_total_elapsed"] == _CONSUMED


def test_the_step_hands_the_reducer_its_routing_timing(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """The reducer enforces the deadline from this argument and nowhere else."""
    captured = _drive_step(monkeypatch, tmp_path)

    assert captured.reduce_kwargs
    assert all(timing is not None for timing in captured.reduce_kwargs)
    assert captured.reduce_kwargs[0].total_elapsed_seconds == _CONSUMED


def test_the_step_folds_its_elapsed_time_into_the_cycle(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """The consumed total only advances because the step folds each step in."""
    captured = _drive_step(monkeypatch, tmp_path)

    assert captured.folded
    assert all(delta >= 0.0 for delta in captured.folded)
