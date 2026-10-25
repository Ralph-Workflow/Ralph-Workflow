"""Outer auto-integration retains an active merge while resolvers cool down."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from git import Repo

from ralph.config.models import UnifiedConfig
from ralph.git.merge import MERGE_STATE_IN_PROGRESS, merge_state
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_record import read_record
from ralph.pipeline.rebase_state import RebaseOutcomeKind, RebaseState
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]

_TARGET = "main"
_PHASE = "rebase_conflict_resolution"


class _FrozenClock:
    def monotonic(self) -> float:
        return 1_000.0


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {"general": {"auto_integrate_enabled": True, "auto_integrate_target": _TARGET}}
    )


def _conflicted_repo(root: Path) -> Path:
    repo = Repo.init(root, initial_branch=_TARGET)
    with repo.config_writer() as writer:
        writer.set_value("user", "name", "Test User")
        writer.set_value("user", "email", "test@example.com")
    shared = root / "shared.txt"
    shared.write_text("base\n", encoding="utf-8")
    repo.index.add([shared.name])
    repo.index.commit("base")
    feature = repo.create_head("feature")
    shared.write_text("target\n", encoding="utf-8")
    repo.index.add([shared.name])
    repo.index.commit("target")
    repo.head.reference = feature
    repo.head.reset(index=True, working_tree=True)
    shared.write_text("feature\n", encoding="utf-8")
    repo.index.add([shared.name])
    repo.index.commit("feature")
    return root


def _declines(_root: Path, _target: str) -> bool:
    return False


def test_all_resolvers_on_cooldown_retain_active_merge_as_pending(tmp_path: Path) -> None:
    root = _conflicted_repo(tmp_path / "repo")
    clock = _FrozenClock()
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=3)
    candidates = ("agent-a", "agent-b")
    scope = WorkspaceScope(root)

    first = auto_integrate_after_commit(
        _config(),
        scope,
        RebaseState(),
        conflict_resolver=_declines,
        ledger=ledger,
        candidates=candidates,
        sleep=lambda _seconds: None,
        jitter=lambda: 0.0,
    )
    assert first is not None
    assert merge_state(root) == MERGE_STATE_IN_PROGRESS
    assert read_record(root) is not None

    for agent in candidates:
        for _attempt in range(3):
            ledger.record(AttemptFact(phase=_PHASE, agent=agent, artifact_valid=False))

    outcome = auto_integrate_after_commit(
        _config(),
        scope,
        RebaseState(),
        conflict_resolver=_declines,
        ledger=ledger,
        candidates=candidates,
        sleep=lambda _seconds: None,
        jitter=lambda: 0.0,
    )

    assert outcome is not None
    assert outcome.last_outcome == RebaseOutcomeKind.CONFLICT
    assert outcome.recovery_record_retained
    assert not outcome.is_intervention_required
    assert not outcome.fast_forwarded
    assert outcome.consecutive_conflicts == 0
    assert outcome.last_reason is not None
    assert "currently eligible" in outcome.last_reason
    assert "cooldown" in outcome.last_reason
    assert "budget exhausted" not in outcome.last_reason
    assert merge_state(root) == MERGE_STATE_IN_PROGRESS
    retained = read_record(root)
    assert retained is not None
    assert retained.resolving_merge
