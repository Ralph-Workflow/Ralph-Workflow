"""Bounded-recovery tests for the diagnostic-repair attempt cap.

The production loop kept rotating inspection agents without ever
reaching a terminal state. The fix is a module-level cap; once
the cap is reached the diagnostic path emits
``RAISE_DIAGNOSTIC_INTERVENTION`` and refuses to call any more
agents.
"""

from __future__ import annotations

import importlib
from pathlib import Path

from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    record_path,
)
from tests.test_auto_integrate_record_idempotent import _RecordBackend

diagnostic = importlib.import_module("ralph.pipeline._integration_diagnostic")


def _record(merge_commit_repair_attempts: int = 0) -> IntegrationRecord:
    return IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature",
        pre_target_sha="target",
        resolving_rebase=True,
        merge_commit_repair_attempts=merge_commit_repair_attempts,
    )


def test_cap_emits_intervention_transition() -> None:
    """Reaching the cap emits ``RAISE_DIAGNOSTIC_INTERVENTION`` on the record."""
    backend = _RecordBackend()
    root = Path("/workspace")
    record = _record(merge_commit_repair_attempts=diagnostic.DIAGNOSTIC_REPAIR_ATTEMPT_CAP)
    invoked: list[str] = []

    def invoke(agent: str, _prompt: Path) -> bool:
        invoked.append(agent)
        return True

    diagnostic.handoff_integration_diagnostic(
        root=root,
        record=record,
        failure="completion receipt unreadable",
        agents=("first", "second"),
        invoke=invoke,
        backend=backend,
    )

    assert invoked == []
    persisted = IntegrationRecord.model_validate_json(backend.read_bytes(record_path(root)))
    assert persisted.diagnostic_intervention_required is True
    assert "diagnostic repair exhausted" in (persisted.repair_cap_reason or "")


def test_cap_does_not_increment_attempt_counter() -> None:
    """The over-cap handoff must not silently rotate the counter."""
    backend = _RecordBackend()
    root = Path("/workspace")
    record = _record(merge_commit_repair_attempts=diagnostic.DIAGNOSTIC_REPAIR_ATTEMPT_CAP + 1)

    diagnostic.handoff_integration_diagnostic(
        root=root,
        record=record,
        failure="completion receipt unreadable",
        agents=("first", "second"),
        invoke=lambda *_: True,
        backend=backend,
    )

    persisted = IntegrationRecord.model_validate_json(backend.read_bytes(record_path(root)))
    assert persisted.merge_commit_repair_attempts == diagnostic.DIAGNOSTIC_REPAIR_ATTEMPT_CAP + 1
    assert persisted.diagnostic_intervention_required is True


def test_below_cap_still_invokes_agent() -> None:
    """A record below the cap still calls the next agent in the chain."""
    backend = _RecordBackend()
    root = Path("/workspace")
    record = _record(merge_commit_repair_attempts=0)
    invoked: list[str] = []

    def invoke(agent: str, _prompt: Path) -> bool:
        invoked.append(agent)
        return True

    diagnostic.handoff_integration_diagnostic(
        root=root,
        record=record,
        failure="completion receipt unreadable",
        agents=("first", "second", "third"),
        invoke=invoke,
        backend=backend,
    )

    assert invoked == ["first"]
    persisted = IntegrationRecord.model_validate_json(backend.read_bytes(record_path(root)))
    assert persisted.merge_commit_repair_attempts == 1
    assert persisted.diagnostic_intervention_required is False


def test_cap_rotates_agents_then_emits_intervention() -> None:
    """End-to-end: cap+1 handoffs end in intervention, with no agent over-call."""
    backend = _RecordBackend()
    root = Path("/workspace")
    record = _record(merge_commit_repair_attempts=0)
    invoked: list[str] = []

    def invoke(agent: str, _prompt: Path) -> bool:
        invoked.append(agent)
        return True

    total_attempts = diagnostic.DIAGNOSTIC_REPAIR_ATTEMPT_CAP + 1
    for _ in range(total_attempts):
        record = (
            IntegrationRecord.model_validate_json(backend.read_bytes(record_path(root)))
            if backend.exists(record_path(root))
            else record
        )
        diagnostic.handoff_integration_diagnostic(
            root=root,
            record=record,
            failure="unreadable",
            agents=("first", "second"),
            invoke=invoke,
            backend=backend,
        )

    # The first ``DIAGNOSTIC_REPAIR_ATTEMPT_CAP`` calls invoke
    # agents; the (cap+1)-th call emits intervention and calls
    # none.
    assert len(invoked) == diagnostic.DIAGNOSTIC_REPAIR_ATTEMPT_CAP
    persisted = IntegrationRecord.model_validate_json(backend.read_bytes(record_path(root)))
    assert persisted.diagnostic_intervention_required is True
    assert persisted.merge_commit_repair_attempts == diagnostic.DIAGNOSTIC_REPAIR_ATTEMPT_CAP


def test_default_cap_is_five() -> None:
    """The default cap is 5 (low enough to break the production loop fast)."""
    assert diagnostic.DIAGNOSTIC_REPAIR_ATTEMPT_CAP == 5


def test_no_agents_returns_false_without_clearing() -> None:
    """A record with no candidate agents returns False without raising."""
    backend = _RecordBackend()
    root = Path("/workspace")
    record = _record()
    result = diagnostic.handoff_integration_diagnostic(
        root=root,
        record=record,
        failure="completion receipt unreadable",
        agents=(),
        invoke=lambda *_: True,
        backend=backend,
    )
    assert result is False
    # The record was not persisted (no agent to call, no
    # intervention raised).
    assert not backend.exists(record_path(root))


def test_intervention_transition_does_not_change_ownership_fingerprint() -> None:
    """The legitimate ``RAISE_DIAGNOSTIC_INTERVENTION`` transition must
    NOT change the ownership fingerprint.

    ``diagnose_retained_integration`` re-reads the persisted record
    after a diagnostic handoff and reverts to the pre-handoff
    record whenever the fingerprint changed. If the fingerprint
    calculation does not exclude the diagnostic-path-owned fields
    (``diagnostic_intervention_required``, ``repair_cap_reason``),
    the legitimate intervention transition would change the
    fingerprint and the re-read would silently revert the
    intervention -- re-creating the production loop the cap was
    designed to break.
    """
    from ralph.pipeline.operation_evidence import ownership_fingerprint

    record = _record(merge_commit_repair_attempts=0)
    fingerprint_before = ownership_fingerprint(record)

    intervened = record.with_transition(
        __import__(
            "ralph.pipeline.auto_integrate_record", fromlist=["RAISE_DIAGNOSTIC_INTERVENTION"]
        ).RAISE_DIAGNOSTIC_INTERVENTION,
        diagnostic_intervention_required=True,
        repair_cap_reason="diagnostic repair exhausted; operator intervention required",
    )
    fingerprint_after = ownership_fingerprint(intervened)

    # The legitimate intervention transition must NOT alter the
    # fingerprint. The diagnostic path's authority transition is
    # not external modification of the record's identity.
    assert fingerprint_after == fingerprint_before
    assert intervened.diagnostic_intervention_required is True
    assert intervened.repair_cap_reason is not None
