"""Tests for the single-owner failure ledger audit (audit_agent_failure_single_owner)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from ralph.testing.audit_agent_failure_single_owner import (
    audit_production_source,
    audit_test_source,
)


def test_clean_production_code_passes() -> None:
    source = """
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, AttemptFact

def handle_attempt(ledger: AgentFailureLedger, fact: AttemptFact) -> None:
    outcome = ledger.record(fact)
    if outcome.demoted:
        pass
"""
    violations = audit_production_source("ralph/pipeline/runner.py", source)
    assert not violations


def test_competing_technical_retry_cap_assignment_flagged() -> None:
    source = """
class MyController:
    def __init__(self):
        self.technical_retry_cap = 5
"""
    violations = audit_production_source("ralph/recovery/custom_controller.py", source)
    assert len(violations) >= 1
    assert any(
        v.rule == "production_streak_counter_state" and "technical_retry_cap" in v.detail
        for v in violations
    )


def test_competing_technical_retry_cap_private_assignment_flagged() -> None:
    source = """
class MyController:
    def __init__(self):
        self._technical_retry_cap = 10
"""
    violations = audit_production_source("ralph/recovery/custom_controller.py", source)
    assert len(violations) >= 1
    assert any(
        v.rule == "production_streak_counter_state" and "_technical_retry_cap" in v.detail
        for v in violations
    )


def test_competing_technical_retry_cap_function_parameter_flagged() -> None:
    source = """
def execute_retry(phase: str, technical_retry_cap: int = 5) -> None:
    pass
"""
    violations = audit_production_source("ralph/pipeline/retry_handler.py", source)
    assert len(violations) >= 1
    assert any(
        v.rule == "production_streak_counter_state" and "technical_retry_cap" in v.detail
        for v in violations
    )


def test_competing_technical_retry_cap_attribute_access_flagged() -> None:
    source = """
def check_limit(ctrl: object) -> bool:
    return ctrl.technical_retry_cap > 0
"""
    violations = audit_production_source("ralph/pipeline/retry_guard.py", source)
    assert len(violations) >= 1
    assert any(
        v.rule == "production_streak_counter_state" and "technical_retry_cap" in v.detail
        for v in violations
    )


def test_competing_technical_retry_cap_call_arg_flagged() -> None:
    source = """
def setup_controller():
    return make_controller(technical_retry_cap=3)
"""
    violations = audit_production_source("ralph/pipeline/init_step.py", source)
    assert len(violations) >= 1
    assert any(
        v.rule == "production_streak_counter_state" and "technical_retry_cap" in v.detail
        for v in violations
    )


def test_competing_resolver_retry_counter_flagged() -> None:
    source = """
class ConflictResolutionSession:
    def __init__(self):
        self.resolver_retries = 0
        self.resolver_retry_count = 0
        self.resolver_retry_cap = 3
        self.resolver_health_counter = 0
"""
    violations = audit_production_source("ralph/pipeline/conflict_resolution/session.py", source)
    assert len(violations) >= 4
    for name in (
        "resolver_retries",
        "resolver_retry_count",
        "resolver_retry_cap",
        "resolver_health_counter",
    ):
        assert any(name in v.detail for v in violations), f"Expected violation for {name}"


def test_forbidden_tracker_class_flagged() -> None:
    source = """
class SameShapeRetryTracker:
    pass
"""
    violations = audit_production_source("ralph/recovery/tracker.py", source)
    assert len(violations) >= 1
    assert any(v.rule == "production_streak_counter_state" for v in violations)


def test_forbidden_tracker_import_flagged() -> None:
    source = """
from ralph.recovery._same_shape_retry_tracker import SameShapeRetryTracker
"""
    violations = audit_production_source("ralph/pipeline/step.py", source)
    assert len(violations) >= 1
    assert any(v.rule == "production_legacy_tracker_import" for v in violations)


def test_test_clean_public_ledger_usage_passes() -> None:
    source = """
def test_ledger(ledger):
    status = ledger.status("dev", "agent_a")
    assert status.is_eligible
    outcome = ledger.record(fact)
    assert outcome.demoted is False
    snap = ledger.snapshot()
    restored = AgentFailureLedger.from_snapshot(snap)
    assert restored.is_available("dev", "agent_a")
"""
    violations = audit_test_source("tests/recovery/test_sample.py", source)
    assert not violations


def test_test_private_ledger_attribute_flagged() -> None:
    source = """
def test_private(ledger):
    entries = ledger._entries
    count = ledger._consecutive_unproductive
"""
    violations = audit_test_source("tests/recovery/test_sample.py", source)
    assert len(violations) >= 2
    assert all(v.rule == "test_private_ledger_access" for v in violations)


def test_test_getattr_private_ledger_attribute_flagged() -> None:
    source = """
def test_getattr(ledger):
    entries = getattr(ledger, "_entries")
"""
    violations = audit_test_source("tests/recovery/test_sample.py", source)
    assert len(violations) == 1
    assert violations[0].rule == "test_private_ledger_access"


def test_test_competing_keyword_arg_flagged() -> None:
    source = """
def test_controller():
    ctrl = make_controller(technical_retry_cap=5)
"""
    violations = audit_test_source("tests/recovery/test_other.py", source)
    assert len(violations) == 1
    assert violations[0].rule == "test_deleted_tracker_access"
    assert "technical_retry_cap" in violations[0].detail


def test_transitional_allowed_file_bypasses_check(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.testing import audit_agent_failure_single_owner as audit_mod

    monkeypatch.setattr(
        audit_mod,
        "TEMPORARY_TRANSITIONAL_RULES",
        {"ralph/recovery/controller.py": frozenset({"_technical_retry_cap"})},
    )
    source = """
class RecoveryController:
    def __init__(self):
        self._technical_retry_cap = 10
"""
    violations = audit_production_source("ralph/recovery/controller.py", source)
    assert not violations
