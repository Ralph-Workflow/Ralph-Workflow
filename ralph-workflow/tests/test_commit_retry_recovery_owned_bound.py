from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.recovery.agent_failure_ledger import AgentFailureLedger, Cooldown
from tests import test_commit_retry_cooldown as commit_retry_spec

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch


def test_rebuilt_owner_outer_attempt_bound_tracks_recovery_policy(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    make_pipeline_deps = commit_retry_spec.make_test_pipeline_deps

    def make_pipeline_deps_with_two_attempt_policy(*args: object, **kwargs: object) -> object:
        deps = make_pipeline_deps(*args, **kwargs)
        clock = kwargs["recovery_clock"]
        return dataclasses.replace(
            deps,
            ledger=AgentFailureLedger(clock=clock, consecutive_limit=2),
        )

    monkeypatch.setattr(
        commit_retry_spec,
        "make_test_pipeline_deps",
        make_pipeline_deps_with_two_attempt_policy,
    )

    run = commit_retry_spec._run_failing_commit_route(
        tmp_path,
        monkeypatch,
        rebuild_owner_per_attempt=True,
    )

    assert len(run.owners) == 2
    assert len(run.agent_invocations) == 2
    assert all(
        not isinstance(
            owner.ledger.status(commit_retry_spec._COMMIT_PHASE, commit_retry_spec._COMMIT_AGENT_NAME),
            Cooldown,
        )
        for owner in run.owners
    )
