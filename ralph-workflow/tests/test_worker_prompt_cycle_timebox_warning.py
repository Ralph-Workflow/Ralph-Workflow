"""Cycle-timebox warning on a parallel worker's prompt.

Fan-out workers materialize their prompts through the prepared-prompt path,
which never carried the timebox warning: a worker could spend the tail of the
cycle budget with no idea a deadline was approaching, while the serial
development agent doing the same work was warned.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pipeline.effects import PreparePromptEffect
from ralph.pipeline.prompt_prep import materialize_prepared_prompt
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path as _Path

_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
_WARNED_CONSUMED_SECONDS = 5760.0



@lru_cache(maxsize=1)
def _bundle() -> object:
    """Load the bundled policy once; these tests share a hard time budget."""
    return load_policy(_DEFAULTS_DIR)


def _materialize(state: PipelineState, tmp_path: _Path) -> dict[str, object]:
    bundle = _bundle()
    captured: dict[str, object] = {}

    def fake_materialize(**kwargs: object) -> str:
        captured.update(kwargs)
        return "fake-prompt.md"

    materialize_prepared_prompt(
        PreparePromptEffect(phase="development", drain="development"),
        bundle.pipeline,
        bundle.artifacts,
        WorkspaceScope(str(tmp_path)),
        agents_policy=bundle.agents,
        state=state,
        env={},
        materialize_fn=fake_materialize,
    )
    return captured


def test_worker_prompt_carries_the_warning_past_the_threshold(tmp_path: Path) -> None:
    """A worker started past 80% of the budget is warned like the serial agent."""
    captured = _materialize(
        PipelineState(
            phase="development",
            cycle_timebox_active=True,
            cycle_timebox_consumed_seconds=_WARNED_CONSUMED_SECONDS,
        ),
        tmp_path,
    )

    warning = captured.get("cycle_timebox_warning")
    assert isinstance(warning, dict)
    assert warning["remaining_seconds"] == 1440.0


def test_worker_prompt_is_clean_before_the_threshold(tmp_path: Path) -> None:
    captured = _materialize(
        PipelineState(
            phase="development",
            cycle_timebox_active=True,
            cycle_timebox_consumed_seconds=60.0,
        ),
        tmp_path,
    )

    assert captured.get("cycle_timebox_warning") is None


def test_worker_prompt_is_clean_with_no_cycle_running(tmp_path: Path) -> None:
    captured = _materialize(
        PipelineState(
            phase="development",
            cycle_timebox_active=False,
            cycle_timebox_consumed_seconds=_WARNED_CONSUMED_SECONDS,
        ),
        tmp_path,
    )

    assert captured.get("cycle_timebox_warning") is None
