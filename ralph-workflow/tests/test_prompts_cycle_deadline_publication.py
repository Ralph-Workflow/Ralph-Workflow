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
    CYCLE_DURATION_SECONDS_ENV,
    CYCLE_FINALIZATION_TARGET_ENV,
    CYCLE_WARN_EPOCH_ENV,
)
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.prompt_prep import cycle_timebox_warning_from_env
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


def test_publication_is_withdrawn_once_the_invocation_returns(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """The deadline belongs to one invocation, not to everything spawned after it.

    The environment is process-global, so a deadline left published after the
    development invocation is inherited by every later subprocess — including
    agents with nothing to do with the cycle, such as the auto-integrate
    conflict resolver, which would be told to wrap up work it never started.
    """
    _reserve_env(monkeypatch)
    state = PipelineState(
        phase="development",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=_ELAPSED_SECONDS,
    )
    _materialize("development", state, tmp_path, cycle_total_elapsed=_ELAPSED_SECONDS)
    assert CYCLE_DEADLINE_EPOCH_ENV in os.environ

    runner_module.withdraw_cycle_deadline_env()

    assert CYCLE_WARN_EPOCH_ENV not in os.environ
    assert CYCLE_DEADLINE_EPOCH_ENV not in os.environ
    assert CYCLE_FINALIZATION_TARGET_ENV not in os.environ


def test_publish_then_withdraw_across_two_invocations(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Sequencing, not just the endpoints: a real publish must be revoked later.

    The two tests above seed the environment by hand; this one publishes for a
    guarded invocation and then runs a later, unguarded one, which is the shape
    a stale deadline would actually leak through.
    """
    _reserve_env(monkeypatch)
    _materialize(
        "development",
        PipelineState(
            phase="development",
            cycle_timebox_active=True,
            cycle_timebox_consumed_seconds=_ELAPSED_SECONDS,
        ),
        tmp_path,
        cycle_total_elapsed=_ELAPSED_SECONDS,
    )
    assert CYCLE_DEADLINE_EPOCH_ENV in os.environ

    _materialize(
        "planning",
        PipelineState(phase="planning", cycle_timebox_active=False),
        tmp_path,
        cycle_total_elapsed=0.0,
    )

    assert CYCLE_WARN_EPOCH_ENV not in os.environ
    assert CYCLE_DEADLINE_EPOCH_ENV not in os.environ
    assert CYCLE_FINALIZATION_TARGET_ENV not in os.environ


def test_fan_out_publishes_the_deadline_for_its_workers(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Fan-out spawns worker bridges without materializing a phase prompt.

    Without publishing on that path a fanned-out cycle gives its workers no
    deadline at all, while still leaking whatever the previous invocation left
    behind.
    """
    _reserve_env(monkeypatch)
    state = PipelineState(
        phase="development",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=_ELAPSED_SECONDS,
    )

    runner_module.publish_cycle_deadline_env(
        state, "development", _bundle(), _ELAPSED_SECONDS
    )

    assert CYCLE_DEADLINE_EPOCH_ENV in os.environ
    assert os.environ[CYCLE_FINALIZATION_TARGET_ENV] == "development_final_commit_cleanup"


def test_fan_out_withdraws_a_stale_deadline_when_no_cycle_runs(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _reserve_env(monkeypatch)

    runner_module.publish_cycle_deadline_env(
        PipelineState(phase="development", cycle_timebox_active=False),
        "development",
        _bundle(),
        0.0,
    )

    assert CYCLE_DEADLINE_EPOCH_ENV not in os.environ



def test_finalizing_an_agent_invocation_keeps_the_deadline_readable(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """The finalizer grades the agent's result, so it must NOT revoke first.

    Grading promotes and validates the result artifact, and the warned
    incomplete-work gate in that validation asks the runtime — not the agent —
    whether this cycle had warned. Revoking here answered "no" for every
    promoted artifact, disabling the gate on the path it exists for. The
    step's ``finally`` revokes instead; see
    ``tests/test_runner_cycle_deadline_revocation.py``.
    """
    _reserve_env(monkeypatch)
    _materialize(
        "development",
        PipelineState(
            phase="development",
            cycle_timebox_active=True,
            cycle_timebox_consumed_seconds=_ELAPSED_SECONDS,
        ),
        tmp_path,
        cycle_total_elapsed=_ELAPSED_SECONDS,
    )
    assert CYCLE_DEADLINE_EPOCH_ENV in os.environ

    state, event = runner_module.finalize_agent_invocation(
        effect=InvokeAgentEffect(
            agent_name="claude",
            phase="development",
            prompt_file="PROMPT.md",
            drain="development",
            chain_name="development",
        ),
        event=PipelineEvent.AGENT_FAILURE,
        state=PipelineState(phase="development"),
        config=None,
        policy_bundle=_bundle(),
        workspace=FsWorkspace(tmp_path),
        workspace_scope=None,
        display=None,
        display_context=None,
        verbosity=None,
        recovery_controller=None,
        run_id=None,
    )

    assert CYCLE_DEADLINE_EPOCH_ENV in os.environ
    assert event == PipelineEvent.AGENT_FAILURE
    assert state.phase == "development"


def test_the_published_deadline_round_trips_into_a_worker_warning(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Producer and consumer must agree, not just each match a hand-built dict.

    The worker rebuilds its warning from what the pipeline published, so a
    missing or renamed published value silently leaves every fan-out worker
    unwarned.
    """
    _reserve_env(monkeypatch)
    # 80% of the bundled 7200s budget has elapsed: the warning point is now.
    _materialize(
        "development",
        PipelineState(
            phase="development",
            cycle_timebox_active=True,
            cycle_timebox_consumed_seconds=5760.0,
        ),
        tmp_path,
        cycle_total_elapsed=5760.0,
    )

    warning = cycle_timebox_warning_from_env(os.environ, now_epoch=time.time())

    assert warning is not None
    assert warning["duration_seconds"] == 7200.0
    assert 1380.0 <= float(str(warning["remaining_seconds"])) <= 1440.0


def test_withdrawing_clears_every_published_name(monkeypatch: MonkeyPatch) -> None:
    """A partial withdrawal is worse than none: consumers read a mixed deadline.

    The duration and finalization target are published alongside the epochs and
    are just as inheritable, so leaving either behind describes one cycle with
    another's numbers. Whether the runner actually calls this on each exit path
    is pinned separately in ``tests/test_runner_cycle_deadline_revocation.py``.
    """
    _reserve_env(monkeypatch)
    _publish_a_guarded_deadline()
    assert CYCLE_DEADLINE_EPOCH_ENV in os.environ

    runner_module.withdraw_cycle_deadline_env()

    for name in (
        CYCLE_WARN_EPOCH_ENV,
        CYCLE_DEADLINE_EPOCH_ENV,
        CYCLE_FINALIZATION_TARGET_ENV,
        CYCLE_DURATION_SECONDS_ENV,
    ):
        assert name not in os.environ


def _publish_a_guarded_deadline() -> None:
    runner_module.publish_cycle_deadline_env(
        PipelineState(
            phase="development",
            cycle_timebox_active=True,
            cycle_timebox_consumed_seconds=_ELAPSED_SECONDS,
        ),
        "development",
        _bundle(),
        _ELAPSED_SECONDS,
    )


def test_the_warning_reaches_the_prompt_materializer(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Publishing the deadline is not the same as warning the agent.

    The prompt's own warning is a separate argument handed to the materializer,
    and every test here injected a materializer that discarded it — so the
    argument could be replaced with None, silencing the development prompt,
    with this whole file still green.
    """
    _reserve_env(monkeypatch)
    captured: dict[str, object] = {}
    workspace = FsWorkspace(tmp_path)
    workspace.write("PROMPT.md", "Do the work")
    registry = MagicMock()
    registry.get.return_value = None

    def _capture(**kwargs: object) -> str:
        captured.update(kwargs)
        return "fake-prompt.md"

    # 80% of the bundled 7200s budget has elapsed: the warning point is now.
    runner_module.materialize_agent_prompt_if_needed(
        InvokeAgentEffect(
            agent_name="claude",
            phase="development",
            prompt_file="PROMPT.md",
            drain="development",
            chain_name="development",
        ),
        PipelineState(
            phase="development",
            cycle_timebox_active=True,
            cycle_timebox_consumed_seconds=5760.0,
        ),
        workspace,
        _bundle(),
        registry,
        materialize_fn=_capture,
        cycle_total_elapsed=5760.0,
    )

    warning = captured["cycle_timebox_warning"]
    assert warning is not None
    assert warning["finalization_target"] == "development_final_commit_cleanup"


def test_the_prepared_prompt_path_also_warns(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """The second prompt path must warn too, and only one of them was pinned.

    A `PreparePromptEffect` re-prompt renders through a different call site
    than an agent invocation. Its warning argument could be replaced with None
    — silencing every re-prompted development agent inside a warned cycle —
    with the invocation path's coverage still green.
    """
    from ralph.pipeline.effects import PreparePromptEffect
    from ralph.pipeline.prompt_prep import materialize_prepared_prompt
    from ralph.workspace.scope import WorkspaceScope

    _reserve_env(monkeypatch)
    captured: dict[str, object] = {}
    workspace = FsWorkspace(tmp_path)
    workspace.write("PROMPT.md", "Do the work")
    bundle = _bundle()

    materialize_prepared_prompt(
        PreparePromptEffect(phase="development", drain="development"),
        bundle.pipeline,
        bundle.artifacts,
        WorkspaceScope(tmp_path),
        agents_policy=bundle.agents,
        # 80% of the bundled 7200s budget is spent: the warning point is now.
        state=PipelineState(
            phase="development",
            cycle_timebox_active=True,
            cycle_timebox_consumed_seconds=5760.0,
        ),
        materialize_fn=lambda **kwargs: captured.update(kwargs) or "fake-prompt.md",
    )

    warning = captured["cycle_timebox_warning"]
    assert warning is not None
    assert warning["finalization_target"] == "development_final_commit_cleanup"


def test_the_fan_out_phase_publishes_before_spawning_its_workers(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Workers inherit the deadline at spawn or never see it at all.

    The publication helper was pinned by calling it directly, so the fan-out
    call site itself could be deleted — leaving every worker in a warned cycle
    unwarned — with that coverage still green.
    """
    from ralph.pipeline.effects import FanOutEffect
    from ralph.workspace.scope import WorkspaceScope

    _reserve_env(monkeypatch)
    published: list[str | None] = []
    integration_saw: list[str | None] = []

    def _fan_out(**kwargs: object) -> PipelineState:
        # The raw value, not mere presence: the fixture seeds these names with
        # a placeholder, so presence alone is satisfied by publishing nothing.
        published.append(os.environ.get(CYCLE_DEADLINE_EPOCH_ENV))
        # Run the completion callback the real fan-out runs. Auto-integration
        # spawns a conflict-resolver agent that never joined the cycle, and
        # stubbing this away left the coordinator's side of that rule untested
        # while the worker's twin was pinned.
        on_complete = kwargs["_on_successful_completion"]
        on_complete(PipelineState(phase="development"))
        return PipelineState(phase="development")

    monkeypatch.setattr(runner_module, "execute_fan_out_sync", _fan_out)
    monkeypatch.setattr(
        runner_module,
        "_integrate_after_fan_out",
        lambda **kwargs: (
            integration_saw.append(os.environ.get(CYCLE_DEADLINE_EPOCH_ENV))
            or kwargs["state"]
        ),
    )

    runner_module._run_fan_out_phase(
        effect=FanOutEffect(phase="development", work_units=(), max_workers=1),
        state=PipelineState(
            phase="development",
            cycle_timebox_active=True,
            cycle_timebox_consumed_seconds=_ELAPSED_SECONDS,
        ),
        display=None,
        policy_bundle=_bundle(),
        workspace_scope=WorkspaceScope(tmp_path),
        pipeline_subscriber=None,
        config=MagicMock(),
        config_path=None,
        cli_overrides=None,
        monitor_stop_cb=None,
        pipeline_deps=None,
        registry=MagicMock(),
        display_context=None,
        routing_timing=runner_module.RoutingTiming(
            total_elapsed_seconds=_ELAPSED_SECONDS
        ),
    )

    assert len(published) == 1
    assert published[0] is not None
    # Hidden from the conflict resolver reconciliation spawns, which never
    # joined this cycle.
    assert integration_saw == [None]
    # A real epoch, which the placeholder is not.
    assert float(published[0]) > time.time()
    # And revoked afterwards, so nothing spawned later inherits it.
    assert CYCLE_DEADLINE_EPOCH_ENV not in os.environ


def test_the_warning_point_is_announced_to_the_operator(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """The operator learns a cycle passed 80% from this line and nowhere else.

    Demoting it to debug removes it from normal output entirely, which no test
    noticed — so the run would look unremarkable right up to the redirect.
    """
    from loguru import logger

    _reserve_env(monkeypatch)
    records: list[str] = []
    sink_id = logger.add(lambda message: records.append(str(message)), level="WARNING")
    try:
        _materialize(
            "development",
            PipelineState(
                phase="development",
                cycle_timebox_active=True,
                cycle_timebox_consumed_seconds=5760.0,
            ),
            tmp_path,
            cycle_total_elapsed=5760.0,
        )
    finally:
        logger.remove(sink_id)

    assert any("cycle-timebox warning" in record for record in records)
