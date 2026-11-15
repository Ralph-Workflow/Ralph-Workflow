from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.recovery.agent_failure_ledger import AgentFailureLedger, Cooldown
from tests import test_commit_retry_cooldown as commit_retry_spec
from tests.test_commit_retry_cooldown import _COMMIT_AGENT_NAME, _COMMIT_PHASE

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch

    from ralph.agents.clock import Clock
    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.pipeline.factory import PipelineDeps


def test_rebuilt_owner_outer_attempt_bound_tracks_recovery_policy(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    make_pipeline_deps = commit_retry_spec.make_test_pipeline_deps

    def make_pipeline_deps_with_two_attempt_policy(
        display_context: DisplayContext,
        *,
        registry_factory: Callable[[UnifiedConfig], object] | None = None,
        recovery_clock: Clock | None = None,
    ) -> PipelineDeps:
        assert recovery_clock is not None
        deps = make_pipeline_deps(
            display_context,
            registry_factory=registry_factory,
            recovery_clock=recovery_clock,
        )
        return dataclasses.replace(
            deps,
            ledger=AgentFailureLedger(clock=recovery_clock, consecutive_limit=2),
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
            owner.ledger.status(
                _COMMIT_PHASE,
                _COMMIT_AGENT_NAME,
            ),
            Cooldown,
        )
        for owner in run.owners
    )
