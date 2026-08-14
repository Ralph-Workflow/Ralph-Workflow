"""Publication of the cycle deadline to the agent-facing MCP server.

The MCP server runs as a separate process spawned per agent invocation, so it
cannot read pipeline state. The deadline is fixed for the lifetime of one
invocation, so the pipeline publishes it as wall-clock epochs in the
environment that subprocess inherits, and withdraws it when the invocation is
not inside a guarded cycle.
"""

from __future__ import annotations

import os
import time
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.mcp.protocol.env import (
    CYCLE_DEADLINE_EPOCH_ENV,
    CYCLE_FINALIZATION_TARGET_ENV,
    CYCLE_WARN_EPOCH_ENV,
)
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pytest import MonkeyPatch

_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
_ELAPSED_SECONDS = 3600.0
# Bundled budget is 7200s with the warning derived at 80% (5760s).
_EXPECTED_SECONDS_TO_WARNING = 2160.0
_EXPECTED_WARNING_TO_DEADLINE = 1440.0
_CLOCK_TOLERANCE_SECONDS = 30.0



@lru_cache(maxsize=1)
def _bundle() -> object:
    """Load the bundled policy once; these tests share a hard time budget."""
    return load_policy(_DEFAULTS_DIR)


def _reserve_env(monkeypatch: MonkeyPatch) -> None:
    """Register the published names with monkeypatch so the test restores them."""
    for name in (CYCLE_WARN_EPOCH_ENV, CYCLE_DEADLINE_EPOCH_ENV, CYCLE_FINALIZATION_TARGET_ENV):
        monkeypatch.setenv(name, "stale-value")


def _materialize(
    phase: str,
    state: PipelineState,
    tmp_path: Path,
    *,
    cycle_total_elapsed: float | None,
) -> None:
    workspace = FsWorkspace(tmp_path)
    workspace.write("PROMPT.md", "Do the work")
    registry = MagicMock()
    registry.get.return_value = None
    runner_module.materialize_agent_prompt_if_needed(
        InvokeAgentEffect(
            agent_name="claude",
            phase=phase,
            prompt_file="PROMPT.md",
            drain=phase,
            chain_name=phase,
        ),
        state,
        workspace,
        _bundle(),
        registry,
        materialize_fn=lambda **_kwargs: "fake-prompt.md",
        cycle_total_elapsed=cycle_total_elapsed,
    )


def test_guarded_invocation_publishes_warning_and_deadline_epochs(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """A development invocation inside an active cycle publishes both epochs."""
    _reserve_env(monkeypatch)
    state = PipelineState(
        phase="development",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=_ELAPSED_SECONDS,
    )

    before = time.time()
    _materialize("development", state, tmp_path, cycle_total_elapsed=_ELAPSED_SECONDS)

    warn_epoch = float(os.environ[CYCLE_WARN_EPOCH_ENV])
    deadline_epoch = float(os.environ[CYCLE_DEADLINE_EPOCH_ENV])
    assert (
        abs((warn_epoch - before) - _EXPECTED_SECONDS_TO_WARNING) < _CLOCK_TOLERANCE_SECONDS
    )
    assert (
        abs((deadline_epoch - warn_epoch) - _EXPECTED_WARNING_TO_DEADLINE)
        < _CLOCK_TOLERANCE_SECONDS
    )
    assert os.environ[CYCLE_FINALIZATION_TARGET_ENV] == "development_final_commit_cleanup"


def test_invocation_outside_a_cycle_withdraws_a_stale_deadline(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """A planning invocation must not inherit the previous cycle's deadline."""
    _reserve_env(monkeypatch)
    state = PipelineState(phase="planning", cycle_timebox_active=False)

    _materialize("planning", state, tmp_path, cycle_total_elapsed=0.0)

    assert CYCLE_WARN_EPOCH_ENV not in os.environ
    assert CYCLE_DEADLINE_EPOCH_ENV not in os.environ
    assert CYCLE_FINALIZATION_TARGET_ENV not in os.environ


def test_deadline_is_withdrawn_once_the_cycle_concludes(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Finalization phases run with no deadline published."""
    _reserve_env(monkeypatch)
    state = PipelineState(
        phase="development_final_commit_cleanup",
        cycle_timebox_active=False,
        cycle_timebox_consumed_seconds=7200.0,
    )

    _materialize(
        "development_final_commit_cleanup", state, tmp_path, cycle_total_elapsed=7200.0
    )

    assert CYCLE_DEADLINE_EPOCH_ENV not in os.environ


def test_untimed_invocation_withdraws_the_deadline(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """With no timing context there is no deadline to advertise."""
    _reserve_env(monkeypatch)
    state = PipelineState(
        phase="development",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=_ELAPSED_SECONDS,
    )

    _materialize("development", state, tmp_path, cycle_total_elapsed=None)

    assert CYCLE_WARN_EPOCH_ENV not in os.environ
    assert CYCLE_DEADLINE_EPOCH_ENV not in os.environ
    assert CYCLE_FINALIZATION_TARGET_ENV not in os.environ
