from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.pipeline import escalate_record_fact_outcome
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.integration_diagnostic import handoff_integration_diagnostic
from ralph.pipeline.integration_ladder import LAST_RESORT_TERMINAL_NO_SIGNATURE
from ralph.pipeline.integration_owner import RecordFactOutcome
from ralph.pipeline.operation_evidence import (
    InMemoryOperationEvidenceCollector,
    OperationFact,
)
from tests._fake_git_clock import FakeClock
from tests._integration_ladder_fakes import (
    _FakePlumbing,
    _FakeQuarantineFs,
    _ledger,
    _seed_terminal_rungs,
)
from tests._resolver_controller import make_resolver_controller
from tests.test_auto_integrate_record_idempotent import _RecordBackend

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.timeout_seconds(30)

_TERMINAL_STATUS_MARKER = "Integration ladder terminal state:"

_DESTRUCTIVE_GIT_CALLS = (
    "reset_hard",
    "clean_untracked",
    "abort_operation",
    "remove_operation_state",
    "restore_tree_to_head",
    "recreate_worktree",
    "remove_stale_locks",
)


@contextmanager
def _captured_records() -> Iterator[list[tuple[str, str]]]:
    records: list[tuple[str, str]] = []
    handler_id = logger.add(
        lambda message: records.append(
            (str(message.record["level"].name), str(message.record["message"]))
        ),
        level="INFO",
    )
    try:
        yield records
    finally:
        logger.remove(handler_id)


def _record(**overrides: object) -> IntegrationRecord:
    base: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "integrated_feature_sha": "c" * 40,
        "rebase_continue_stop": "d" * 40,
        "rebase_continue_head": "e" * 40,
        "rebase_continue_tree": "f" * 40,
        "merge_commit_parent": "0" * 40,
    }
    base.update(overrides)
    return IntegrationRecord.model_validate(base)


def test_terminal_escalation_reports_one_status_and_repeats_nothing(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _seed_terminal_rungs(ledger)
    git = _FakePlumbing(changed=())
    record = _record()

    with _captured_records() as captured:
        first = escalate_record_fact_outcome(
            tmp_path,
            record,
            RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT,
            reason="diagnostic budget spent",
            ledger=ledger,
            git=git,
            fs=_FakeQuarantineFs(),
            clock=FakeClock(),
            state_dir=tmp_path / ".agent",
        )
        second = escalate_record_fact_outcome(
            tmp_path,
            record,
            RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT,
            reason="diagnostic budget spent",
            ledger=ledger,
            git=git,
            fs=_FakeQuarantineFs(),
            clock=FakeClock(),
            state_dir=tmp_path / ".agent",
        )

    terminal = [message for _level, message in captured if _TERMINAL_STATUS_MARKER in message]
    critical = [level for level, _message in captured if level == "CRITICAL"]
    assert first.outcome == "terminal"
    assert first.reason == LAST_RESORT_TERMINAL_NO_SIGNATURE
    assert second.outcome == "terminal"
    assert second.reason == LAST_RESORT_TERMINAL_NO_SIGNATURE
    assert len(terminal) == 1, captured
    assert critical == [], captured
    assert not any(call.startswith(_DESTRUCTIVE_GIT_CALLS) for call in git.calls), git.calls
    assert git.calls.count("changed_paths") == 1, git.calls
    assert (
        ledger.read().ladder_evidence["rung:last_resort_recovery"]
        == LAST_RESORT_TERMINAL_NO_SIGNATURE
    )


def test_terminal_escalation_reopens_when_the_observed_head_changes(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _seed_terminal_rungs(ledger)
    record = _record()

    with _captured_records() as captured:
        escalate_record_fact_outcome(
            tmp_path,
            record,
            RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT,
            reason="diagnostic budget spent",
            ledger=ledger,
            git=_FakePlumbing(changed=(), head="abc123"),
            fs=_FakeQuarantineFs(),
            clock=FakeClock(),
            state_dir=tmp_path / ".agent",
        )
        moved = escalate_record_fact_outcome(
            tmp_path,
            record,
            RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT,
            reason="diagnostic budget spent",
            ledger=ledger,
            git=_FakePlumbing(changed=(), head="def456"),
            fs=_FakeQuarantineFs(),
            clock=FakeClock(),
            state_dir=tmp_path / ".agent",
        )

    terminal = [message for _level, message in captured if _TERMINAL_STATUS_MARKER in message]
    assert moved.outcome == "terminal"
    assert moved.reason == LAST_RESORT_TERMINAL_NO_SIGNATURE
    assert len(terminal) == 2, captured


def test_terminal_status_names_the_operator_facts_once(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _seed_terminal_rungs(ledger)
    git = _FakePlumbing(changed=())

    with _captured_records() as captured:
        escalate_record_fact_outcome(
            tmp_path,
            _record(),
            RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT,
            reason="diagnostic budget spent",
            ledger=ledger,
            git=git,
            fs=_FakeQuarantineFs(),
            clock=FakeClock(),
            state_dir=tmp_path / ".agent",
        )

    terminal = [message for _level, message in captured if _TERMINAL_STATUS_MARKER in message]
    assert len(terminal) == 1, captured
    message = terminal[0]
    assert "observed HEAD abc123" in message
    assert f"integrated_feature_sha={'c' * 40}" in message
    assert f"pre_target_sha={'b' * 40}" in message
    assert f"pre_feature_sha={'a' * 40}" in message
    assert f"rebase_continue_stop={'d' * 40}" in message
    assert f"rebase_continue_head={'e' * 40}" in message
    assert f"rebase_continue_tree={'f' * 40}" in message
    assert f"merge_commit_parent={'0' * 40}" in message
    assert "preserved copy: nothing to preserve at HEAD abc123" in message
    assert "ralph --resume" in message
    assert "no preserved copy yet" not in message
    assert "the run continues without waiting on a person" not in message


def test_diagnostic_pre_pass_starts_no_second_round_after_the_terminal_state(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    _seed_terminal_rungs(ledger)
    git = _FakePlumbing(changed=())
    record = _record(diagnostic_intervention_required=True)
    invoked: list[str] = []

    def fake_invoke(agent: str, prompt: Path) -> bool:
        invoked.append(agent)
        return False

    backend = _RecordBackend()
    collector = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="rebase_object_resolvable", value="True"),)
    )
    controller = make_resolver_controller()

    with _captured_records() as captured:
        first = handoff_integration_diagnostic(
            controller=controller,
            root=tmp_path,
            record=record,
            failure="retained integration after the diagnostic budget was spent",
            agents=("test-resolver",),
            invoke=fake_invoke,
            backend=backend,
            collector=collector,
            ledger=ledger,
            git=git,
            fs=_FakeQuarantineFs(),
            clock=FakeClock(),
        )
        second = handoff_integration_diagnostic(
            controller=controller,
            root=tmp_path,
            record=record,
            failure="retained integration after the diagnostic budget was spent",
            agents=("test-resolver",),
            invoke=fake_invoke,
            backend=backend,
            collector=collector,
            ledger=ledger,
            git=git,
            fs=_FakeQuarantineFs(),
            clock=FakeClock(),
        )

    terminal = [message for _level, message in captured if _TERMINAL_STATUS_MARKER in message]
    critical = [level for level, _message in captured if level == "CRITICAL"]
    assert first is False
    assert second is False
    assert len(terminal) == 1, captured
    assert critical == [], captured
    assert invoked == [], captured
    assert not any(call.startswith(_DESTRUCTIVE_GIT_CALLS) for call in git.calls), git.calls
    assert git.calls.count("changed_paths") == 1, git.calls
    assert (
        ledger.read().ladder_evidence["rung:last_resort_recovery"]
        == LAST_RESORT_TERMINAL_NO_SIGNATURE
    )
