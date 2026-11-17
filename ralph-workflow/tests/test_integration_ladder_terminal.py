from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.pipeline._integration_diagnostic_handoff import escalate_record_fact_outcome
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.integration_decision import CleanupLadderRung
from ralph.pipeline.integration_ladder import (
    LAST_RESORT_TERMINAL_NO_SIGNATURE,
    ensure_preserved_copy,
    preserved_copy_identity,
    run_ladder_rung,
)
from ralph.pipeline.integration_owner import RecordFactOutcome
from tests._fake_git_clock import FakeClock
from tests._integration_ladder_fakes import (
    _evidence,
    _FakePlumbing,
    _FakeQuarantineFs,
    _ledger,
    _seed_terminal_rungs,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.pipeline.integration_attempt_ledger import AttemptLedger

pytestmark = pytest.mark.timeout_seconds(30)

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
def _captured_warnings() -> Iterator[list[str]]:
    messages: list[str] = []
    handler_id = logger.add(
        lambda message: messages.append(str(message.record["message"])),
        level="WARNING",
    )
    try:
        yield messages
    finally:
        logger.remove(handler_id)


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
        "rebase_continue_head": "f" * 40,
        "rebase_continue_stop": "d" * 40,
        "rebase_continue_tree": "1" * 40,
        "merge_commit_parent": "e" * 40,
    }
    base.update(overrides)
    return IntegrationRecord.model_validate(base)


def _exhausted_ledger(tmp_path: Path) -> AttemptLedger:
    ledger = _ledger(tmp_path)
    _seed_terminal_rungs(ledger)
    return ledger


def test_disabled_rung6_ends_the_ladder_in_a_terminal_outcome(tmp_path: Path) -> None:
    ledger = _exhausted_ledger(tmp_path)
    git = _FakePlumbing(changed=())

    with _captured_warnings() as warnings:
        step = escalate_record_fact_outcome(
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

    assert step.rung is CleanupLadderRung.LAST_RESORT
    assert step.outcome == "terminal"
    assert step.outcome != "failed"
    assert step.reason == LAST_RESORT_TERMINAL_NO_SIGNATURE
    assert step.evidence.get("next_strategy") == "completion_in_place"
    assert "NeedsOperator" not in step.reason
    assert not any(call.startswith(_DESTRUCTIVE_GIT_CALLS) for call in git.calls), git.calls
    terminal = [message for message in warnings if "Integration ladder terminal state:" in message]
    assert len(terminal) == 1, warnings
    message = terminal[0]
    assert "NeedsOperator" not in message
    assert "operator action required" not in message
    assert "completion_in_place" in message or "complete_in_place" in message


def test_changed_resolution_key_reopens_the_terminal_attempt(tmp_path: Path) -> None:
    ledger = _exhausted_ledger(tmp_path)
    git = _FakePlumbing(changed=())
    first_key = ("stop-a", ("resolver-a",), "config-a")
    second_key = ("stop-b", ("resolver-a",), "config-a")

    with _captured_records() as captured:
        first = escalate_record_fact_outcome(
            tmp_path,
            _record(resolution_gate_key=first_key),
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
            _record(resolution_gate_key=second_key),
            RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT,
            reason="diagnostic budget spent",
            ledger=ledger,
            git=git,
            fs=_FakeQuarantineFs(),
            clock=FakeClock(),
            state_dir=tmp_path / ".agent",
        )

    terminal = [
        message for _level, message in captured if "Integration ladder terminal state:" in message
    ]
    assert first.outcome == "terminal"
    assert second.outcome == "terminal"
    assert len(terminal) == 2, captured
    assert all("NeedsOperator" not in message for message in terminal), terminal
    assert all("operator action required" not in message for message in terminal), terminal


def test_terminal_operator_message_names_head_expected_shas_and_resume_command(
    tmp_path: Path,
) -> None:
    ledger = _exhausted_ledger(tmp_path)
    git = _FakePlumbing(changed=())

    with _captured_warnings() as warnings:
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

    terminal = [message for message in warnings if "Integration ladder terminal state:" in message]
    assert len(terminal) == 1, warnings
    message = terminal[0]
    assert "observed HEAD abc123" in message
    assert f"integrated_feature_sha={'c' * 40}" in message
    assert f"pre_target_sha={'b' * 40}" in message
    assert f"pre_feature_sha={'a' * 40}" in message
    assert f"rebase_continue_stop={'d' * 40}" in message
    assert f"rebase_continue_head={'f' * 40}" in message
    assert f"rebase_continue_tree={'1' * 40}" in message
    assert f"merge_commit_parent={'e' * 40}" in message
    assert "ralph --resume" in message
    assert "NeedsOperator" not in message
    assert "operator action required" not in message
    assert "no preserved copy yet" not in message
    assert "the run continues without waiting on a person" not in message


def test_decide_ladder_transition_routes_terminal_to_completion_in_place() -> None:
    from ralph.pipeline.integration_decision import CLEANUP_LADDER_ORDER
    from ralph.pipeline.integration_ladder import decide_ladder_transition
    from ralph.pipeline.ladder_facts import LadderFacts
    from ralph.pipeline.ladder_state import LadderState
    from ralph.pipeline.landed_verdict import LandedVerdict

    transition = decide_ladder_transition(
        LadderFacts(
            rung=CleanupLadderRung.LAST_RESORT,
            outcome="terminal",
            reason=LAST_RESORT_TERMINAL_NO_SIGNATURE,
            landed=LandedVerdict.NOT_LANDED,
            enabled_rungs=frozenset(CLEANUP_LADDER_ORDER),
            terminal_for_attempt=False,
            clock_now=0.0,
        )
    )
    assert transition.terminal is True
    assert "NeedsOperator" not in transition.state
    assert transition.state is LadderState.COMPLETE_IN_PLACE


def test_production_escalation_callers_supply_feature_tip() -> None:
    production_root = Path(__file__).resolve().parent.parent / "ralph" / "pipeline"
    sources = {
        "_integration_diagnostic.py": (production_root / "_integration_diagnostic.py").read_text(
            encoding="utf-8"
        ),
        "_integration_diagnostic_prepass.py": (
            production_root / "_integration_diagnostic_prepass.py"
        ).read_text(encoding="utf-8"),
    }
    missing: list[str] = []
    for rel, source in sources.items():
        for index, line in enumerate(source.splitlines(), start=1):
            if "escalate_diagnostic_budget_to_ladder(" not in line:
                continue
            window = "\n".join(source.splitlines()[index - 1 : index + 20])
            if "feature_tip=" not in window:
                missing.append(f"{rel}:{index}")
    assert not missing, (
        "escalation callers must supply feature_tip so completion-in-place is reachable:\n"
        + "\n".join(missing)
    )


def test_terminal_operator_message_states_nothing_to_preserve_on_a_clean_worktree(
    tmp_path: Path,
) -> None:
    ledger = _exhausted_ledger(tmp_path)
    git = _FakePlumbing(changed=())

    with _captured_warnings() as warnings:
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

    message = next(entry for entry in warnings if "Integration ladder terminal state:" in entry)
    assert "preserved copy: nothing to preserve at HEAD abc123" in message
    assert not any(call.startswith(_DESTRUCTIVE_GIT_CALLS) for call in git.calls), git.calls


def test_terminal_operator_message_names_the_real_preserved_identity(tmp_path: Path) -> None:
    ledger = _exhausted_ledger(tmp_path)
    git = _FakePlumbing()

    with _captured_warnings() as warnings:
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

    message = next(entry for entry in warnings if "Integration ladder terminal state:" in entry)
    assert "preserved copy: refs/ralph-backup/fake" in message
    assert "no preserved copy yet" not in message
    assert ledger.read().ladder_evidence["preserved:alternate_preservation"] == (
        "refs/ralph-backup/fake"
    )


def test_non_terminal_escalation_names_a_real_preserved_copy(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    git = _FakePlumbing()

    with _captured_warnings() as warnings:
        escalate_record_fact_outcome(
            tmp_path,
            _record(),
            RecordFactOutcome.STALE_INTENT,
            reason="stale retained intent",
            ledger=ledger,
            git=git,
            fs=_FakeQuarantineFs(),
            clock=FakeClock(),
            state_dir=tmp_path / ".agent",
        )

    message = next(entry for entry in warnings if "escalated autonomously" in entry)
    assert "preserved copy: refs/ralph-backup/fake" in message
    assert "no preserved copy yet" not in message


def test_reset_capable_rung_creates_the_preserved_copy_before_the_reset(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    git = _FakePlumbing()

    result = run_ladder_rung(
        CleanupLadderRung.QUARANTINE,
        root=tmp_path,
        ledger=ledger,
        git=git,
        fs=_FakeQuarantineFs(),
        clock=FakeClock(),
        state_dir=tmp_path / ".agent",
        paths=("src/locked.py",),
        verify_post_condition=lambda _condition: True,
    )

    assert result.outcome == "succeeded"
    order = [call for call in git.calls if call in ("commit_dirt", "restore_tree_to_head")]
    assert order == ["commit_dirt", "restore_tree_to_head"]
    assert preserved_copy_identity(result, ledger.read()) == "refs/ralph-backup/fake"


def test_reset_capable_rung_is_refused_when_no_preserved_copy_can_be_created(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    git = _FakePlumbing(commit_ref=None, readable_refs=frozenset(), fail_patch=True)

    result = run_ladder_rung(
        CleanupLadderRung.QUARANTINE,
        root=tmp_path,
        ledger=ledger,
        git=git,
        fs=_FakeQuarantineFs(),
        clock=FakeClock(),
        state_dir=tmp_path / ".agent",
        paths=("src/locked.py",),
    )

    assert result.outcome == "failed"
    assert result.reason.startswith("failed:no-preserved-copy:")
    assert "restore_tree_to_head" not in git.calls
    assert "preserved:alternate_preservation" not in ledger.read().ladder_evidence


def test_ensure_preserved_copy_is_a_no_op_when_the_ledger_already_holds_one(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    git = _FakePlumbing()
    first = ensure_preserved_copy(
        tmp_path,
        ledger=ledger,
        git=git,
        fs=_FakeQuarantineFs(),
        state_dir=tmp_path / ".agent",
    )

    assert first is not None
    assert first.preserved_ref == "refs/ralph-backup/fake"
    calls_after_first = list(git.calls)
    second = ensure_preserved_copy(
        tmp_path,
        ledger=ledger,
        git=git,
        fs=_FakeQuarantineFs(),
        state_dir=tmp_path / ".agent",
    )

    assert second is None
    assert git.calls == calls_after_first


def test_unchanged_retained_facts_end_in_exactly_one_terminal_round(tmp_path: Path) -> None:
    ledger = _exhausted_ledger(tmp_path)
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
        after_first_round = len(captured)
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
        after_second_round = len(captured)
        third = escalate_record_fact_outcome(
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

    terminal = [
        message for _level, message in captured if "Integration ladder terminal state:" in message
    ]
    critical = [level for level, _message in captured if level == "CRITICAL"]
    assert first.outcome == "terminal"
    assert second.outcome == "terminal"
    assert third.outcome == "terminal"
    assert len(terminal) == 1, captured
    assert critical == [], captured
    assert not any(
        "Integration ladder terminal state:" in message
        for _level, message in captured[after_first_round:after_second_round]
    ), captured
    assert not any(
        "Integration ladder terminal state:" in message
        for _level, message in captured[after_second_round:]
    ), captured
    assert _evidence(tmp_path)["rung:last_resort_recovery"] == LAST_RESORT_TERMINAL_NO_SIGNATURE
    assert LAST_RESORT_TERMINAL_NO_SIGNATURE in terminal[0]
    assert "NeedsOperator" not in terminal[0]
    assert "operator action required" not in terminal[0]
