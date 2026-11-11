from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import pytest

from ralph.pipeline.integration_attempt_ledger import (
    ATTEMPT_LEDGER_FILENAME,
    AttemptLedger,
    AttemptLedgerState,
    LedgerRebuildFacts,
    attempt_ledger_path,
    rebuild_state_from_git_facts,
)
from ralph.pipeline.integration_decision import EscalationRung

pytestmark = pytest.mark.timeout_seconds(30)


def test_attempt_ledger_persists_state_across_restart(tmp_path: Path) -> None:
    ledger = AttemptLedger(tmp_path)
    advanced = ledger.with_attempt("sig-a", "land", is_unknown=False)
    advanced = ledger.with_attempt("sig-a", "land", is_unknown=False)
    next_state = ledger.with_attempt("sig-a", "land", is_unknown=False)
    del advanced

    fresh = AttemptLedger(tmp_path)
    restored = fresh.read()

    assert restored.rung is not EscalationRung.REPAIR, (
        f"restart should preserve rung advance, got {restored.rung}"
    )
    assert restored.repeat_counts.get(("sig-a", "land")) == 3
    assert restored.unknown_attempts == 0
    assert next_state.repeat_counts.get(("sig-a", "land")) == 3


def test_attempt_ledger_unknown_attempts_persist_across_restart(tmp_path: Path) -> None:
    ledger = AttemptLedger(tmp_path, unknown_budget=4)
    for _ in range(2):
        ledger.with_attempt("sig-unknown", "wait", is_unknown=True)

    fresh = AttemptLedger(tmp_path, unknown_budget=4)
    restored = fresh.read()

    assert restored.unknown_attempts == 2
    assert restored.unknown_budget == 4


def test_attempt_ledger_new_session_or_record_reload_never_restores_spent_budget(
    tmp_path: Path,
) -> None:
    session1 = AttemptLedger(tmp_path, unknown_budget=3, repeat_threshold=2)
    session1.with_attempt("sig-1", "action-1", is_unknown=True)
    session1.with_attempt("sig-1", "action-1", is_unknown=True)
    spent_state = session1.with_attempt("sig-1", "action-1", is_unknown=True)
    assert spent_state.unknown_attempts == 3

    session1.with_attempt("sig-2", "action-2", is_unknown=False)
    advanced_state = session1.with_attempt("sig-2", "action-2", is_unknown=False)
    assert advanced_state.rung is EscalationRung.DISPATCH_RESOLVER
    assert advanced_state.repeat_counts[("sig-2", "action-2")] == 2

    del session1

    new_session = AttemptLedger(tmp_path, unknown_budget=3, repeat_threshold=2)
    reloaded_state = new_session.read()

    assert reloaded_state.unknown_attempts == 3
    assert reloaded_state.unknown_budget == 3
    assert reloaded_state.rung is EscalationRung.DISPATCH_RESOLVER
    assert reloaded_state.repeat_counts[("sig-2", "action-2")] == 2

    next_state = new_session.with_attempt("sig-2", "action-2", is_unknown=False)
    assert next_state.rung is EscalationRung.REBUILD_ON_TARGET
    assert next_state.repeat_counts[("sig-2", "action-2")] == 3


def test_attempt_ledger_default_state_when_file_missing(tmp_path: Path) -> None:
    fresh = AttemptLedger(tmp_path)
    state = fresh.read()

    assert state.rung is EscalationRung.REPAIR
    assert state.unknown_attempts == 0
    assert state.unknown_budget == 3
    assert state.repeat_threshold == 2
    assert state.repeat_counts == OrderedDict()


def test_attempt_ledger_path_anchors_under_agent_directory(tmp_path: Path) -> None:
    path = attempt_ledger_path(tmp_path)
    assert path.name == ATTEMPT_LEDGER_FILENAME
    assert path.parent.name == ".agent"


def test_attempt_ledger_recovers_from_corrupt_json(tmp_path: Path) -> None:
    ledger_path = attempt_ledger_path(tmp_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text("{not valid json", encoding="utf-8")

    fresh = AttemptLedger(tmp_path)
    restored = fresh.read()

    assert restored.rung is EscalationRung.REPAIR
    assert restored.unknown_attempts == 0


def test_attempt_ledger_recovers_when_root_is_unreadable(tmp_path: Path) -> None:
    ledger_path = attempt_ledger_path(tmp_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_bytes(b"\x00\xff\xfe\xab not utf-8")

    fresh = AttemptLedger(tmp_path)
    restored = fresh.read()

    assert restored.rung is EscalationRung.REPAIR


def test_attempt_ledger_recovers_when_payload_is_not_a_dict(tmp_path: Path) -> None:
    ledger_path = attempt_ledger_path(tmp_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text('["not", "a", "dict"]', encoding="utf-8")

    fresh = AttemptLedger(tmp_path)
    restored = fresh.read()

    assert restored.rung is EscalationRung.REPAIR


def test_attempt_ledger_older_schema_version_is_migrated_forward(tmp_path: Path) -> None:
    ledger_path = attempt_ledger_path(tmp_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        (
            "{"
            '"version": 0,'
            '"rung": "dispatch_resolver",'
            '"unknown_attempts": 1,'
            '"unknown_budget": 5,'
            '"repeat_threshold": 2,'
            '"repeat_counts": [[["sig-1", "land"], 4], [["sig-2", "wait"], 1]]'
            "}"
        ),
        encoding="utf-8",
    )

    fresh = AttemptLedger(tmp_path)
    restored = fresh.read()

    assert restored.version == 1
    assert restored.rung is EscalationRung.DISPATCH_RESOLVER
    assert restored.unknown_attempts == 1
    assert restored.unknown_budget == 5
    assert restored.repeat_counts.get(("sig-1", "land")) == 4


def test_attempt_ledger_lenient_parser_does_not_reset_rung_upward(tmp_path: Path) -> None:
    ledger_path = attempt_ledger_path(tmp_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        (
            "{"
            '"version": 1,'
            '"rung": "not a real rung",'
            '"unknown_attempts": 0,'
            '"unknown_budget": 3,'
            '"repeat_threshold": 2,'
            '"repeat_counts": []'
            "}"
        ),
        encoding="utf-8",
    )

    restored = AttemptLedger(tmp_path).read()

    assert restored.rung is EscalationRung.REPAIR


def test_attempt_ledger_corrupt_repeat_entry_drops_only_the_bad_entry(tmp_path: Path) -> None:
    ledger_path = attempt_ledger_path(tmp_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        (
            "{"
            '"version": 1,'
            '"rung": "repair",'
            '"unknown_attempts": 0,'
            '"unknown_budget": 3,'
            '"repeat_threshold": 2,'
            '"repeat_counts": ['
            '[["sig-good", "land"], 2],'
            '["this is not a list"],'
            '[["sig-also-good", "commit"], 1]'
            "]"
            "}"
        ),
        encoding="utf-8",
    )

    restored = AttemptLedger(tmp_path).read()

    assert restored.repeat_counts.get(("sig-good", "land")) == 2
    assert restored.repeat_counts.get(("sig-also-good", "commit")) == 1
    assert len(restored.repeat_counts) == 2


def test_attempt_ledger_with_attempt_persists_high_water_before_returning(
    tmp_path: Path,
) -> None:
    ledger = AttemptLedger(tmp_path)
    state = ledger.with_attempt("sig-write-ahead", "repair", is_unknown=False)

    persisted = AttemptLedger(tmp_path).read()
    assert persisted.repeat_counts.get(("sig-write-ahead", "repair")) == 1
    assert state.repeat_counts.get(("sig-write-ahead", "repair")) == 1
    assert state.repeat_threshold == persisted.repeat_threshold


def test_attempt_ledger_with_attempt_unknown_only_consumes_unknown_budget(
    tmp_path: Path,
) -> None:
    ledger = AttemptLedger(tmp_path)
    state = ledger.with_attempt("sig-unknowned", "wait", is_unknown=True)

    assert state.repeat_counts == OrderedDict()
    assert state.unknown_attempts == 1
    assert state.rung is EscalationRung.REPAIR


def test_attempt_ledger_rung_advances_only_at_threshold(tmp_path: Path) -> None:
    ledger = AttemptLedger(tmp_path, repeat_threshold=2)
    one = ledger.with_attempt("sig-thresh", "repair", is_unknown=False)
    assert one.rung is EscalationRung.REPAIR

    two = ledger.with_attempt("sig-thresh", "repair", is_unknown=False)
    assert two.rung is EscalationRung.DISPATCH_RESOLVER

    three = ledger.with_attempt("sig-thresh", "repair", is_unknown=False)
    assert three.rung is EscalationRung.REBUILD_ON_TARGET

    four = ledger.with_attempt("sig-thresh", "repair", is_unknown=False)
    assert four.rung is EscalationRung.CONTINUE_WITH_INTEGRATION_PENDING


def test_attempt_ledger_rung_monotone_after_terminal(tmp_path: Path) -> None:
    ledger = AttemptLedger(tmp_path, repeat_threshold=1)
    for _ in range(8):
        ledger.with_attempt("sig-terminal", "wait", is_unknown=False)

    final = AttemptLedger(tmp_path).read()
    assert final.rung is EscalationRung.CONTINUE_WITH_INTEGRATION_PENDING
    assert final.repeat_counts.get(("sig-terminal", "wait")) == 8


def test_attempt_ledger_repeat_counts_bounded_by_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ralph.pipeline.integration_attempt_ledger as ledger_mod

    test_cap = 10
    monkeypatch.setattr(ledger_mod, "REPEAT_CAP", test_cap)

    ledger = AttemptLedger(tmp_path, repeat_threshold=9999)
    for index in range(test_cap + 5):
        ledger.with_attempt(f"sig-{index}", "wait", is_unknown=False)

    persisted = AttemptLedger(tmp_path).read()

    assert len(persisted.repeat_counts) == test_cap
    assert ("sig-0", "wait") not in persisted.repeat_counts
    assert ("sig-4", "wait") not in persisted.repeat_counts
    last_index = test_cap + 4
    assert (f"sig-{last_index}", "wait") in persisted.repeat_counts


def test_attempt_ledger_state_rejects_invalid_field_values() -> None:
    with pytest.raises(ValueError, match="unknown_attempts"):
        AttemptLedgerState(unknown_attempts=-1)
    with pytest.raises(ValueError, match="unknown_budget"):
        AttemptLedgerState(unknown_budget=0)
    with pytest.raises(ValueError, match="repeat_threshold"):
        AttemptLedgerState(repeat_threshold=0)
    with pytest.raises(ValueError, match="version"):
        AttemptLedgerState(version=0)


def test_attempt_ledger_rejects_invalid_constructor_arguments(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown_budget"):
        AttemptLedger(tmp_path, unknown_budget=0)
    with pytest.raises(ValueError, match="repeat_threshold"):
        AttemptLedger(tmp_path, repeat_threshold=0)


def test_attempt_ledger_rebuilds_from_git_facts_at_the_strongest_safe_rung(
    tmp_path: Path,
) -> None:
    ledger_path = attempt_ledger_path(tmp_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text("{corrupt ledger", encoding="utf-8")

    facts = LedgerRebuildFacts(
        proven_rung=EscalationRung.DISPATCH_RESOLVER,
        proven_repeats=(("sig-a", "land", 2),),
    )
    rebuilt = AttemptLedger(tmp_path).rebuild_from_git_facts(facts)

    assert rebuilt.rung is EscalationRung.DISPATCH_RESOLVER
    assert rebuilt.repeat_counts[("sig-a", "land")] == 2
    assert rebuilt.unknown_attempts == 0, "the budget is fresh, not carried"
    reread = AttemptLedger(tmp_path).read()
    assert reread.rung is EscalationRung.DISPATCH_RESOLVER
    assert reread.repeat_counts[("sig-a", "land")] == 2
    quarantine = ledger_path.with_name(ATTEMPT_LEDGER_FILENAME + ".quarantine")
    assert quarantine.read_text(encoding="utf-8") == "{corrupt ledger"


def test_attempt_ledger_rebuild_without_facts_resumes_at_the_least_permissive_rung(
    tmp_path: Path,
) -> None:
    ledger_path = attempt_ledger_path(tmp_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text("not json at all", encoding="utf-8")

    rebuilt = AttemptLedger(tmp_path).rebuild_from_git_facts(LedgerRebuildFacts())

    assert rebuilt.rung is EscalationRung.REPAIR
    assert rebuilt.repeat_counts == OrderedDict()
    assert rebuilt.unknown_attempts == 0

    charged = AttemptLedger(tmp_path).with_attempt("sig-rebuilt", "land", is_unknown=False)
    assert charged.rung is EscalationRung.REPAIR
    assert charged.repeat_counts[("sig-rebuilt", "land")] == 1


def test_attempt_ledger_rebuild_state_drops_unproven_and_negative_counts() -> None:
    facts = LedgerRebuildFacts(
        proven_rung=EscalationRung.CONTINUE_WITH_INTEGRATION_PENDING,
        proven_repeats=(("sig-good", "land", 3), ("sig-bad", "land", -1)),
    )

    rebuilt = rebuild_state_from_git_facts(facts)

    assert rebuilt.rung is EscalationRung.CONTINUE_WITH_INTEGRATION_PENDING
    assert list(rebuilt.repeat_counts.items()) == [(("sig-good", "land"), 3)]
    assert rebuilt.unknown_attempts == 0
