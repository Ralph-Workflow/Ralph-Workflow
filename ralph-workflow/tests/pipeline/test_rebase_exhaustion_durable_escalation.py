from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import pytest
from loguru import logger

from ralph.agents.timeout_clock import FakeClock
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline import auto_integrate_rebase_merge as rebase_merge
from ralph.pipeline import resolve_conflicted_rebase
from ralph.pipeline.auto_integrate_record import IntegrationRecord, write_record
from ralph.pipeline.conflict_resolution import ResolutionTerminationReason
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.round_verdict import (
    resolution_exhaustion_reason,
    round_termination_reason,
)
from ralph.pipeline.integration_attempt_ledger import AttemptLedger
from ralph.pipeline.integration_ladder import ladder_charge_count
from ralph.pipeline.run_loop import run_auto_integrate_recovery_preamble
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact, Spent
from ralph.workspace.scope import WorkspaceScope

pytestmark = pytest.mark.timeout_seconds(5)

_CANDIDATES = ("agent1", "agent2")


def _set_spent_chain_terminal_reason(session: Any) -> None:
    ledger = AgentFailureLedger(clock=FakeClock(), total_limit=1)
    for candidate in _CANDIDATES:
        ledger.record(AttemptFact(PHASE_RESOLUTION, candidate, artifact_valid=False))
        assert isinstance(ledger.status(PHASE_RESOLUTION, candidate), Spent)
    session.ledger = ledger
    session.unresolved_paths = ("app.py",)
    session.terminal_reason = round_termination_reason(
        session,
        invoked=False,
        candidates=_CANDIDATES,
    )
    session.exhaustion_reason = resolution_exhaustion_reason(
        session,
        session.unresolved_paths,
    )


class _TerminalRunner:
    def __init__(self, terminal_reason: ResolutionTerminationReason) -> None:
        self.terminal_reason = terminal_reason

    def resolve_rebase(
        self,
        root: Path,
        target: str,
        resolver: Any,
        *,
        session: Any,
    ) -> bool:
        if self.terminal_reason is ResolutionTerminationReason.CANDIDATE_EXHAUSTED:
            _set_spent_chain_terminal_reason(session)
        else:
            session.terminal_reason = self.terminal_reason
            session.unresolved_paths = ("app.py",)
        if self.terminal_reason is ResolutionTerminationReason.CONFIG_FAILURE:
            session.exhaustion_reason = "fix .agent/agents.toml then run ralph --check-config"
            session.charge_conflict_budget = False
            session.last_failure_cause = ResolutionFailureCause.CONFIG_FAILURE
        return False


def _setup_rebase(root: Path) -> IntegrationRecord:
    git_dir = root / ".git"
    rebase_dir = git_dir / "rebase-merge"
    rebase_dir.mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/feature\n", encoding="utf-8")
    (git_dir / "config").write_text("[core]\n\trepositoryformatversion = 0\n", encoding="utf-8")
    (git_dir / "objects").mkdir()
    (git_dir / "refs" / "heads").mkdir(parents=True)
    (git_dir / "refs" / "heads" / "feature").write_text("f" * 40, encoding="utf-8")
    (rebase_dir / "head-name").write_text("refs/heads/feature\n", encoding="utf-8")
    (rebase_dir / "orig-head").write_text("f" * 40, encoding="utf-8")
    (rebase_dir / "onto").write_text("a" * 40, encoding="utf-8")
    (rebase_dir / "stopped-sha").write_text("b" * 40, encoding="utf-8")
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="f" * 40,
        pre_target_sha="a" * 40,
        resolving_rebase=True,
        resolving_stop_sha="b" * 40,
    )
    write_record(root, record)
    return record


def _critical_messages() -> tuple[list[str], int]:
    messages: list[str] = []

    def sink(message: object) -> None:
        record = getattr(message, "record", None)
        if record is not None and record["level"].name == "CRITICAL":
            messages.append(str(record["message"]))

    return messages, logger.add(sink, level="CRITICAL")


def test_rebase_exhaustion_regression_continuation_is_durable_and_charges_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _setup_rebase(tmp_path)
    ledger = AttemptLedger(tmp_path)
    before = ladder_charge_count(ledger.read())

    def exhausted_resolve(root: Path, target: str, resolver: object, session: Any = None) -> bool:
        _set_spent_chain_terminal_reason(session)
        return False

    monkeypatch.setattr(rebase_merge, "resolve_rebase_in_progress", exhausted_resolve)
    messages, sink_id = _critical_messages()
    try:
        first = run_auto_integrate_recovery_preamble(
            WorkspaceScope(tmp_path), rebase_stop_resolver=lambda *_args: False
        )
        assert first is not None
        assert first.resolution_exhausted is True
        assert first.resolution_exhaustion_reason is not None
        assert first.resolution_exhaustion_reason.startswith("CANDIDATE_EXHAUSTED: ")
        assert first.recovery_record_retained is False
        assert "retained for next agent" not in (first.last_reason or "")
        after = ladder_charge_count(AttemptLedger(tmp_path).read())
        assert (before, after) == (0, 1)

        critical_count = len(messages)
        repeated = run_auto_integrate_recovery_preamble(
            WorkspaceScope(tmp_path), rebase_stop_resolver=lambda *_args: False
        )
        assert repeated is not None
        assert len(messages) == critical_count
    finally:
        with contextlib.suppress(Exception):
            logger.remove(sink_id)


def test_rebase_exhaustion_regression_initial_attempt_is_durable_and_charges_once(
    tmp_path: Path,
) -> None:
    _setup_rebase(tmp_path)
    before = ladder_charge_count(AttemptLedger(tmp_path).read())

    result = resolve_conflicted_rebase(
        tmp_path,
        "main",
        lambda *_args: False,
        None,
        None,
        agent_runner=_TerminalRunner(ResolutionTerminationReason.CANDIDATE_EXHAUSTED),
    )

    assert result is not None and result.short_circuit is not None
    state = result.short_circuit
    assert state.resolution_exhausted is True
    assert state.resolution_exhaustion_reason is not None
    assert state.resolution_exhaustion_reason.startswith("CANDIDATE_EXHAUSTED: ")
    assert state.recovery_record_retained is False
    assert ladder_charge_count(AttemptLedger(tmp_path).read()) == before + 1


def test_rebase_exhaustion_regression_config_failure_is_exempt_from_durable_charge(
    tmp_path: Path,
) -> None:
    _setup_rebase(tmp_path)
    before = ladder_charge_count(AttemptLedger(tmp_path).read())

    result = resolve_conflicted_rebase(
        tmp_path,
        "main",
        lambda *_args: False,
        None,
        None,
        agent_runner=_TerminalRunner(ResolutionTerminationReason.CONFIG_FAILURE),
    )

    assert result is not None and result.short_circuit is not None
    state = result.short_circuit
    assert state.resolution_exhausted is False
    assert state.recovery_record_retained is True
    assert state.last_failure_cause is ResolutionFailureCause.CONFIG_FAILURE
    assert ladder_charge_count(AttemptLedger(tmp_path).read()) == before
