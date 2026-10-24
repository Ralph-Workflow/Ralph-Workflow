"""Restart-proof attempt-ledger tests.

The integration attempt ledger is the durable, restart-proof budget the
director consults before every action. These tests pin its recovery
contract under the four adversarial inputs the production code must
handle:

* a missing or corrupt ledger file → defaults, never blocks;
* an older-version ledger → migrated forward, the rung never resets
  upward;
* restart over a written ledger → counts and budgets survive verbatim;
* write-ahead high-water mark → ``with_attempt`` persists BEFORE the
  caller can observe the in-memory state.

Pure in-memory tests: no subprocess, no real ``time.sleep``, no real
filesystem outside the per-test ``tmp_path``. This is the
``audit_test_policy`` contract.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import pytest

from ralph.pipeline.integration_attempt_ledger import (
    ATTEMPT_LEDGER_FILENAME,
    AttemptLedger,
    AttemptLedgerState,
    attempt_ledger_path,
)
from ralph.pipeline.integration_decision import EscalationRung

pytestmark = pytest.mark.timeout_seconds(30)


# ---------------------------------------------------------------------------
# Restart preserves budget.
# ---------------------------------------------------------------------------


def test_attempt_ledger_persists_state_across_restart(tmp_path: Path) -> None:
    """Writing a state and re-reading returns the same counts and rung."""
    ledger = AttemptLedger(tmp_path)
    advanced = ledger.with_attempt("sig-a", "land", is_unknown=False)
    advanced = ledger.with_attempt("sig-a", "land", is_unknown=False)
    next_state = ledger.with_attempt(
        "sig-a", "land", is_unknown=False
    )  # crosses threshold → rung advance
    del advanced  # ensure no shared mutable state across reads.

    fresh = AttemptLedger(tmp_path)
    restored = fresh.read()

    assert restored.rung is not EscalationRung.REPAIR, (
        f"restart should preserve rung advance, got {restored.rung}"
    )
    assert restored.repeat_counts.get(("sig-a", "land")) == 3
    assert restored.unknown_attempts == 0
    assert next_state.repeat_counts.get(("sig-a", "land")) == 3


def test_attempt_ledger_unknown_attempts_persist_across_restart(tmp_path: Path) -> None:
    """Unknown-attempt budget survives a fresh ``AttemptLedger`` read."""
    ledger = AttemptLedger(tmp_path, unknown_budget=4)
    for _ in range(2):
        ledger.with_attempt("sig-unknown", "wait", is_unknown=True)

    fresh = AttemptLedger(tmp_path, unknown_budget=4)
    restored = fresh.read()

    assert restored.unknown_attempts == 2
    assert restored.unknown_budget == 4


def test_attempt_ledger_default_state_when_file_missing(tmp_path: Path) -> None:
    """A missing ledger yields the first-run default state."""
    fresh = AttemptLedger(tmp_path)
    state = fresh.read()

    assert state.rung is EscalationRung.REPAIR
    assert state.unknown_attempts == 0
    assert state.unknown_budget == 3
    assert state.repeat_threshold == 2
    assert state.repeat_counts == OrderedDict()


def test_attempt_ledger_path_anchors_under_agent_directory(tmp_path: Path) -> None:
    """The ledger lives under ``.agent/`` of the workspace root."""
    path = attempt_ledger_path(tmp_path)
    assert path.name == ATTEMPT_LEDGER_FILENAME
    assert path.parent.name == ".agent"


# ---------------------------------------------------------------------------
# Lenient parsing — corrupt / older-version ledgers never block.
# ---------------------------------------------------------------------------


def test_attempt_ledger_recovers_from_corrupt_json(tmp_path: Path) -> None:
    """A corrupt JSON file yields the default state, no exception raised."""
    ledger_path = attempt_ledger_path(tmp_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text("{not valid json", encoding="utf-8")

    fresh = AttemptLedger(tmp_path)
    restored = fresh.read()

    assert restored.rung is EscalationRung.REPAIR
    assert restored.unknown_attempts == 0


def test_attempt_ledger_recovers_when_root_is_unreadable(tmp_path: Path) -> None:
    """A ledger whose read raises is treated as absent / default."""
    ledger_path = attempt_ledger_path(tmp_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_bytes(b"\x00\xff\xfe\xab not utf-8")

    fresh = AttemptLedger(tmp_path)
    restored = fresh.read()

    assert restored.rung is EscalationRung.REPAIR


def test_attempt_ledger_recovers_when_payload_is_not_a_dict(tmp_path: Path) -> None:
    """A list-shaped payload parses to the default state."""
    ledger_path = attempt_ledger_path(tmp_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text('["not", "a", "dict"]', encoding="utf-8")

    fresh = AttemptLedger(tmp_path)
    restored = fresh.read()

    assert restored.rung is EscalationRung.REPAIR


def test_attempt_ledger_older_schema_version_is_migrated_forward(tmp_path: Path) -> None:
    """An older ``version`` value is bumped to the current schema in memory."""
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
    """A corrupt ``rung`` field falls back to REPAIR, never an unknown ladder member."""
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
    """One malformed repeat_counts entry drops; the rest still parses."""
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


# ---------------------------------------------------------------------------
# Write-ahead high-water mark.
# ---------------------------------------------------------------------------


def test_attempt_ledger_with_attempt_persists_high_water_before_returning(
    tmp_path: Path,
) -> None:
    """The bumped state is visible on disk before ``with_attempt`` returns."""
    ledger = AttemptLedger(tmp_path)
    state = ledger.with_attempt("sig-write-ahead", "repair", is_unknown=False)

    persisted = AttemptLedger(tmp_path).read()
    assert persisted.repeat_counts.get(("sig-write-ahead", "repair")) == 1
    assert state.repeat_counts.get(("sig-write-ahead", "repair")) == 1
    assert state.repeat_threshold == persisted.repeat_threshold


def test_attempt_ledger_with_attempt_unknown_only_consumes_unknown_budget(
    tmp_path: Path,
) -> None:
    """An ``is_unknown=True`` cycle never advances the repeat counter."""
    ledger = AttemptLedger(tmp_path)
    state = ledger.with_attempt("sig-unknowned", "wait", is_unknown=True)

    assert state.repeat_counts == OrderedDict()
    assert state.unknown_attempts == 1
    assert state.rung is EscalationRung.REPAIR


def test_attempt_ledger_rung_advances_only_at_threshold(tmp_path: Path) -> None:
    """Rungs advance at repeat_threshold, never before."""
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
    """Past the terminal rung, repeat pressure never resets the ladder."""
    ledger = AttemptLedger(tmp_path, repeat_threshold=1)
    for _ in range(8):
        ledger.with_attempt("sig-terminal", "wait", is_unknown=False)

    final = AttemptLedger(tmp_path).read()
    assert final.rung is EscalationRung.CONTINUE_WITH_INTEGRATION_PENDING
    assert final.repeat_counts.get(("sig-terminal", "wait")) == 8


# ---------------------------------------------------------------------------
# Bounded accumulator.
# ---------------------------------------------------------------------------


def test_attempt_ledger_repeat_counts_bounded_by_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pushing past the cap keeps the most-recent ``REPEAT_CAP`` keys only."""
    import ralph.pipeline.integration_attempt_ledger as ledger_mod

    test_cap = 10
    monkeypatch.setattr(ledger_mod, "REPEAT_CAP", test_cap)

    ledger = AttemptLedger(tmp_path, repeat_threshold=9999)
    for index in range(test_cap + 5):
        ledger.with_attempt(f"sig-{index}", "wait", is_unknown=False)

    persisted = AttemptLedger(tmp_path).read()

    assert len(persisted.repeat_counts) == test_cap
    # FIFO eviction — the first ``5`` keys must be gone, the most
    # recent ``test_cap`` keys must survive.
    assert ("sig-0", "wait") not in persisted.repeat_counts
    assert ("sig-4", "wait") not in persisted.repeat_counts
    last_index = test_cap + 4
    assert (f"sig-{last_index}", "wait") in persisted.repeat_counts


# ---------------------------------------------------------------------------
# Frozen-dataclass invariants.
# ---------------------------------------------------------------------------


def test_attempt_ledger_state_rejects_invalid_field_values() -> None:
    """``AttemptLedgerState`` enforces non-negative counters and budgets >= 1."""
    with pytest.raises(ValueError, match="unknown_attempts"):
        AttemptLedgerState(unknown_attempts=-1)
    with pytest.raises(ValueError, match="unknown_budget"):
        AttemptLedgerState(unknown_budget=0)
    with pytest.raises(ValueError, match="repeat_threshold"):
        AttemptLedgerState(repeat_threshold=0)
    with pytest.raises(ValueError, match="version"):
        AttemptLedgerState(version=0)


def test_attempt_ledger_rejects_invalid_constructor_arguments(tmp_path: Path) -> None:
    """The owner refuses to bind when budgets are below 1."""
    with pytest.raises(ValueError, match="unknown_budget"):
        AttemptLedger(tmp_path, unknown_budget=0)
    with pytest.raises(ValueError, match="repeat_threshold"):
        AttemptLedger(tmp_path, repeat_threshold=0)
