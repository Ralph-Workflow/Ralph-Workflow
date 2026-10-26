"""Tests for the single-owner failure ledger audit (audit_agent_failure_single_owner)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from ralph.testing.audit_agent_failure_single_owner import (
    PRODUCTION_SCAN_SUBTREES,
    TEMPORARY_TRANSITIONAL_RULES,
    audit_production_source,
    audit_test_source,
    is_transitional_allowed,
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


def test_test_subscript_counter_flagged() -> None:
    source = """
def test_snapshot_counters(controller):
    snap = controller.ledger.snapshot()
    count = snap["entries"]["dev:agent"]["consecutive_unproductive"]
"""
    violations = audit_test_source("tests/test_durability.py", source)
    assert len(violations) >= 1
    assert any(
        v.rule == "test_private_ledger_access" and "consecutive_unproductive" in v.detail
        for v in violations
    )


def test_test_subscript_counter_flagged_in_ledger_unit_test() -> None:
    source = """
def test_snapshot_counters(controller):
    snap = controller.ledger.snapshot()
    count = snap["entries"]["dev:agent"]["consecutive_unproductive"]
"""
    violations = audit_test_source("tests/recovery/test_agent_failure_ledger.py", source)
    assert len(violations) >= 1
    assert any(
        v.rule == "test_private_ledger_access" and "consecutive_unproductive" in v.detail
        for v in violations
    )


def test_test_get_counter_flagged() -> None:
    source = """
def test_get_counters(snap):
    count = snap.get("consecutive_unproductive")
    total = snap.get("total_unproductive")
"""
    violations = audit_test_source("tests/pipeline/test_sample.py", source)
    assert len(violations) >= 2
    assert all(v.rule == "test_private_ledger_access" for v in violations)


def test_transitional_allowed_file_bypasses_check(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.testing import audit_agent_failure_single_owner as audit_mod

    # The transitional rules live in the rule engine the audit imports, so the
    # allowance is added to the shared mapping the engine reads.
    monkeypatch.setitem(
        audit_mod.TEMPORARY_TRANSITIONAL_RULES,
        "ralph/recovery/controller.py",
        frozenset({"_technical_retry_cap"}),
    )
    source = """
class RecoveryController:
    def __init__(self):
        self._technical_retry_cap = 10
"""
    violations = audit_production_source("ralph/recovery/controller.py", source)
    assert not violations


def test_competing_ralph_fault_hits_assignment_flagged() -> None:
    source = """
class ConflictResolutionSession:
    def __init__(self):
        self.ralph_fault_hits = 0
"""
    violations = audit_production_source("ralph/pipeline/conflict_handler.py", source)
    assert len(violations) >= 1
    assert any(
        v.rule == "production_streak_counter_state" and "ralph_fault_hits" in v.detail
        for v in violations
    )


def test_competing_fault_hits_augassign_flagged() -> None:
    source = """
def record_fault(session):
    session.ralph_fault_hits += 1
"""
    violations = audit_production_source("ralph/pipeline/conflict_handler.py", source)
    assert len(violations) >= 1
    assert any("ralph_fault_hits" in v.detail for v in violations)


def test_competing_fault_hits_cap_comparison_flagged() -> None:
    source = """
def check_fault_limit(session):
    if session.ralph_fault_hits < RALPH_FAULT_ESCALATION_HITS:
        return None
"""
    violations = audit_production_source("ralph/pipeline/conflict_handler.py", source)
    assert len(violations) >= 1
    assert any(
        v.rule == "production_streak_counter_state" and "retry cap" in v.detail for v in violations
    )


def test_competing_dead_surfaces_exclusion_flagged() -> None:
    source = """
def bar_agent(session, agent_name):
    session.stop_dead_surfaces = (*session.stop_dead_surfaces, agent_name)
"""
    violations = audit_production_source("ralph/pipeline/conflict_handler.py", source)
    assert len(violations) >= 1
    assert any("stop_dead_surfaces" in v.detail for v in violations)


def test_competing_dead_surfaces_call_mutation_flagged() -> None:
    source = """
def bar_agent(dead_surfaces, agent):
    dead_surfaces.append(agent)
"""
    violations = audit_production_source("ralph/pipeline/conflict_handler.py", source)
    assert len(violations) >= 1
    assert any("dead_surfaces" in v.detail for v in violations)


def test_rc1_fault_hits_and_dead_surfaces_pattern_flagged() -> None:
    source = """
def observe_fault(session, event, agent_name):
    session.ralph_fault_hits += 1
    if session.ralph_fault_hits >= RALPH_FAULT_ESCALATION_HITS:
        session.stop_dead_surfaces = (*session.stop_dead_surfaces, agent_name)
"""
    violations = audit_production_source("ralph/pipeline/custom_observer.py", source)
    assert len(violations) >= 3
    assert any("ralph_fault_hits" in v.detail for v in violations)
    assert any("stop_dead_surfaces" in v.detail for v in violations)


def test_competing_retry_progress_guard_class_flagged() -> None:
    source = """
class RetryProgressGuard:
    pass
"""
    violations = audit_production_source("ralph/pipeline/custom_guard.py", source)
    assert len(violations) >= 1
    assert any(
        v.rule == "production_streak_counter_state" and "RetryProgressGuard" in v.detail
        for v in violations
    )


def test_competing_retry_progress_guard_import_flagged() -> None:
    source = """
from ralph.pipeline._retry_progress_guard import RetryProgressGuard
"""
    violations = audit_production_source("ralph/pipeline/custom_runner.py", source)
    assert len(violations) >= 1
    assert any(
        v.rule == "production_legacy_tracker_import" and "RetryProgressGuard" in v.detail
        for v in violations
    )


def test_competing_progress_guard_attribute_and_param_flagged() -> None:
    source = """
class WorkerSession:
    def __init__(self, progress_guard=None):
        self.progress_guard = progress_guard
"""
    violations = audit_production_source("ralph/pipeline/custom_session.py", source)
    assert len(violations) >= 2
    assert any(
        v.rule == "production_streak_counter_state" and "progress_guard" in v.detail
        for v in violations
    )


def test_competing_retry_failure_signature_function_flagged() -> None:
    source = """
def retry_failure_signature(reason: str, lines: list[str]) -> str:
    return reason
"""
    violations = audit_production_source("ralph/pipeline/custom_sig.py", source)
    assert len(violations) >= 1
    assert any(
        v.rule == "production_streak_counter_state" and "retry_failure_signature" in v.detail
        for v in violations
    )


def test_agents_invoke_retry_failure_signature_is_rejected() -> None:
    source = """
def _retry_failure_signature(exc: Exception) -> tuple[str, str]:
    return type(exc).__name__, str(exc)
"""
    violations = audit_production_source(
        "ralph/agents/invoke/_direct_mcp_recovery.py",
        source,
    )
    assert any(
        v.rule == "production_streak_counter_state" and "_retry_failure_signature" in v.detail
        for v in violations
    )


def test_agents_invoke_is_in_production_scan_scope() -> None:
    assert "ralph/agents/invoke" in PRODUCTION_SCAN_SUBTREES


def test_competing_max_identical_retry_attempts_assignment_and_compare_flagged() -> None:
    source = """
MAX_IDENTICAL_RETRY_ATTEMPTS = 3

def check_attempts(retries: int) -> bool:
    return retries >= MAX_IDENTICAL_RETRY_ATTEMPTS
"""
    violations = audit_production_source("ralph/pipeline/custom_cap.py", source)
    assert len(violations) >= 2
    assert any("MAX_IDENTICAL_RETRY_ATTEMPTS" in v.detail for v in violations)
    assert any("retry cap" in v.detail for v in violations)


def test_test_deleted_progress_guard_tracker_access_and_import_flagged() -> None:
    source = """
from ralph.pipeline._retry_progress_guard import (
    MAX_IDENTICAL_RETRY_ATTEMPTS,
    RetryProgressGuard,
    retry_failure_signature,
)

def test_guard(state):
    guard = state.progress_guard
    assert guard is None
"""
    violations = audit_test_source("tests/custom/test_sample.py", source)
    assert len(violations) >= 4
    assert any(
        v.rule == "test_deleted_tracker_import" and "RetryProgressGuard" in v.detail
        for v in violations
    )
    assert any(
        v.rule == "test_deleted_tracker_import" and "MAX_IDENTICAL_RETRY_ATTEMPTS" in v.detail
        for v in violations
    )
    assert any(
        v.rule == "test_deleted_tracker_import" and "retry_failure_signature" in v.detail
        for v in violations
    )
    assert any(
        v.rule == "test_deleted_tracker_access" and "progress_guard" in v.detail for v in violations
    )


def test_retry_progress_guard_module_banned() -> None:
    violations = audit_production_source("ralph/pipeline/_retry_progress_guard.py", "x = 1")
    assert len(violations) == 1
    assert violations[0].rule == "production_legacy_tracker_module"
    assert "_retry_progress_guard" in violations[0].detail


def test_retry_progress_guard_module_import_banned() -> None:
    source = """
import ralph.pipeline._retry_progress_guard
"""
    violations = audit_production_source("ralph/pipeline/runner.py", source)
    assert len(violations) >= 1
    assert any(
        v.rule == "production_legacy_tracker_import" and "_retry_progress_guard" in v.detail
        for v in violations
    )


def test_transitional_rules_consolidated() -> None:
    # Only surviving conflict resolution files with legitimate needs remain
    assert set(TEMPORARY_TRANSITIONAL_RULES.keys()) == {
        "ralph/pipeline/conflict_resolution/session.py",
        "ralph/pipeline/conflict_resolution/chain_candidates.py",
    }
    # No test files are in transitional rules
    assert not any(k.startswith("tests/") for k in TEMPORARY_TRANSITIONAL_RULES)
    # _driver_rounds.py was fully cleaned up
    assert (
        "ralph/pipeline/conflict_resolution/_driver_rounds.py" not in TEMPORARY_TRANSITIONAL_RULES
    )
    # No deleted U4b modules are in transitional rules
    assert not any("retry_progress_guard" in k for k in TEMPORARY_TRANSITIONAL_RULES)


def test_u4b_bans_permanently_promoted() -> None:
    for sym in (
        "RetryProgressGuard",
        "progress_guard",
        "retry_failure_signature",
        "MAX_IDENTICAL_RETRY_ATTEMPTS",
    ):
        assert not is_transitional_allowed("ralph/pipeline/state.py", sym)
        assert not is_transitional_allowed("tests/test_retry_budget_durability.py", sym)


def test_local_retry_cap_deciding_agent_fate_is_flagged() -> None:
    """A local retry cap compared against a retry counter must be rejected."""
    source = """
def _retry_or_fall_over(state, chain):
    max_retries = 3
    if chain.retries < max_retries:
        new_chain = chain.with_retry_increment()
        new_metrics = state.metrics.with_retry_increment()
        return state.with_phase_chain(state.phase, new_chain).copy_with(metrics=new_metrics), []
    return None
"""
    violations = audit_production_source("ralph/pipeline/reducer.py", source)
    assert any(v.rule == "production_local_retry_cap_decision" for v in violations), (
        f"Expected production_local_retry_cap_decision, got: {violations}"
    )
    flagged = next(v for v in violations if v.rule == "production_local_retry_cap_decision")
    assert flagged.path == "ralph/pipeline/reducer.py"
    assert flagged.line == 4
    assert "chain.retries < max_retries" in flagged.detail


def test_local_retry_cap_with_advance_branch_is_flagged() -> None:
    """The advance branch of a local retry cap decision is rejected too."""
    source = """
def _retry_or_fall_over(state, chain):
    max_retries = 3
    if chain.retries < max_retries:
        return state.with_phase_chain(state.phase, chain.with_retry_increment()), []
    if chain.current_index + 1 < len(chain.agents):
        return state.with_phase_chain(state.phase, chain.with_advance()), []
    return None
"""
    violations = audit_production_source("ralph/pipeline/reducer.py", source)
    assert any(v.rule == "production_local_retry_cap_decision" for v in violations), (
        f"Expected production_local_retry_cap_decision, got: {violations}"
    )


def test_repository_safety_bound_is_not_flagged() -> None:
    """A merge-repair attempt cap bounds repository state, not agent health."""
    source = """
MAX_MERGE_REPAIR_ATTEMPTS = 3

def repair_pending_merge(record):
    if record.merge_commit_repair_attempts < MAX_MERGE_REPAIR_ATTEMPTS:
        return handoff_pending_merge_repair(record)
    return record
"""
    violations = audit_production_source("ralph/pipeline/_pending_merge_repair.py", source)
    assert not violations


def test_retry_budget_without_agent_fate_action_is_not_flagged() -> None:
    """The rule is a decision rule, not a blanket ban on retry identifiers."""
    source = """
MAX_RETRIES = 3

def report_retry_budget(metrics):
    if metrics.total_retries > MAX_RETRIES:
        emit_diagnostic("retry budget exhausted")
"""
    violations = audit_production_source("ralph/pipeline/run_metrics.py", source)
    assert not violations


def test_transport_retry_budget_is_not_flagged() -> None:
    """A transport repetition budget is not an agent-health decision."""
    source = """
def _invocation_retry_allowed_or_raise(retries_used, max_retries):
    if retries_used < max_retries:
        return True
    raise RuntimeError("retry budget exhausted")
"""
    violations = audit_production_source(
        "ralph/agents/invoke/_direct_mcp_recovery.py",
        source,
    )
    assert not violations


def test_test_reading_serialised_ledger_counter_is_flagged() -> None:
    """A test that indexes into the serialised ledger snapshot must be rejected."""
    source = """
def test_snapshot(state):
    assert state.ledger_snapshot["entries"]["development:claude"]["is_spent"] is True
"""
    violations = audit_test_source("tests/recovery/test_ledger_migration.py", source)
    assert any(v.rule == "test_serialised_ledger_access" for v in violations), (
        f"Expected test_serialised_ledger_access, got: {violations}"
    )
    flagged = next(v for v in violations if v.rule == "test_serialised_ledger_access")
    assert flagged.path == "tests/recovery/test_ledger_migration.py"
    assert "is_spent" in flagged.detail


def test_test_reading_serialised_ledger_counter_via_get_is_flagged() -> None:
    """Reading a serialised ledger counter through .get() must be rejected."""
    source = """
def test_snapshot(state):
    reason = state.ledger_snapshot["entries"]["development:claude"].get("spent_reason")
    assert reason is not None
"""
    violations = audit_test_source("tests/recovery/test_ledger_migration.py", source)
    assert any(v.rule == "test_serialised_ledger_access" for v in violations), (
        f"Expected test_serialised_ledger_access, got: {violations}"
    )


def test_test_reading_private_ledger_entry_counter_is_flagged() -> None:
    """A test that reaches a ledger counter through a private attribute is rejected."""
    source = """
def test_private(ledger):
    assert ledger._entries["development:claude"]["is_spent"] is True
"""
    violations = audit_test_source("tests/recovery/test_sample.py", source)
    assert any(v.rule == "test_serialised_ledger_access" for v in violations), (
        f"Expected test_serialised_ledger_access, got: {violations}"
    )


def test_test_public_ledger_answers_are_accepted() -> None:
    """The owner's public answers stay the only facts a test may observe."""
    source = """
def test_public_seam(ledger, fact):
    status = ledger.status("development", "claude")
    assert status.is_spent is False
    assert ledger.is_available("development", "claude")
    outcome = ledger.record(fact)
    assert outcome.demoted is False
    snap = ledger.snapshot()
    restored = AgentFailureLedger.from_snapshot(snap)
    assert restored.is_available("development", "claude")
"""
    violations = audit_test_source("tests/recovery/test_sample.py", source)
    assert not violations


def test_test_private_backoff_counter_access_is_flagged() -> None:
    """A test reading the controller's private backoff counter is rejected."""
    source = """
def test_backoff(controller):
    assert controller._backoff_attempts.get("development:claude") == 2
"""
    violations = audit_test_source("tests/recovery/test_sample.py", source)
    assert len(violations) == 1
    assert violations[0].rule == "test_private_failure_owner_access"
    assert "_backoff_attempts" in violations[0].detail


def test_test_private_unavailability_tracker_access_is_flagged() -> None:
    """A test reading the controller's private unavailability store is rejected."""
    source = """
def test_tracker(controller):
    assert controller._unavailability_tracker.is_available("development", "claude")
"""
    violations = audit_test_source("tests/recovery/test_sample.py", source)
    assert len(violations) == 1
    assert violations[0].rule == "test_private_failure_owner_access"
    assert "_unavailability_tracker" in violations[0].detail


def test_test_private_unavailable_marker_call_is_flagged_in_assignment_context() -> None:
    """The private unavailable marker is rejected outside an assert too."""
    source = """
def test_marker(controller, reason):
    cooldown_ms = controller._mark_agent_unavailable("development", "claude", reason)
    assert cooldown_ms > 0
"""
    violations = audit_test_source("tests/recovery/test_sample.py", source)
    assert len(violations) == 1
    assert violations[0].rule == "test_private_failure_owner_access"
    assert "_mark_agent_unavailable" in violations[0].detail


def test_test_private_backoff_counter_access_via_getattr_is_flagged() -> None:
    """Reading the private backoff counter through getattr() is rejected."""
    source = """
def test_getattr(controller):
    attempts = getattr(controller, "_backoff_attempts")
"""
    violations = audit_test_source("tests/recovery/test_sample.py", source)
    assert len(violations) == 1
    assert violations[0].rule == "test_private_failure_owner_access"
    assert "_backoff_attempts" in violations[0].detail


def test_test_controller_snapshot_private_backoff_read_is_flagged() -> None:
    """Reading the controller's private backoff counter through the snapshot is rejected.

    The serialised snapshot publishes the controller's private failure-owner
    state under a public key name, so indexing that key couples the test to
    the same private state the direct-access rule already rejects.
    """
    source = """
def test_controller_public_seam(controller):
    assert controller.snapshot()["backoff_attempts"] == {}
"""
    violations = audit_test_source("tests/recovery/test_sample.py", source)
    assert any(v.rule == "test_serialised_failure_owner_access" for v in violations), (
        f"Expected test_serialised_failure_owner_access, got: {violations}"
    )
    flagged = next(v for v in violations if v.rule == "test_serialised_failure_owner_access")
    assert flagged.path == "tests/recovery/test_sample.py"
    assert "backoff_attempts" in flagged.detail


def test_test_controller_snapshot_private_backoff_get_is_flagged() -> None:
    """Reading the serialised private backoff counter through .get() is rejected."""
    source = """
def test_snapshot(controller):
    attempts = controller.snapshot().get("backoff_attempts")
"""
    violations = audit_test_source("tests/recovery/test_sample.py", source)
    assert any(v.rule == "test_serialised_failure_owner_access" for v in violations), (
        f"Expected test_serialised_failure_owner_access, got: {violations}"
    )
    assert (
        "backoff_attempts"
        in next(v for v in violations if v.rule == "test_serialised_failure_owner_access").detail
    )


def test_test_controller_snapshot_unavailable_timeouts_read_is_flagged() -> None:
    """The serialised unavailability timeout history is rejected too."""
    source = """
def test_snapshot(controller):
    timeouts = controller.snapshot()["unavailable_timeouts"]
"""
    violations = audit_test_source("tests/recovery/test_sample.py", source)
    assert any(v.rule == "test_serialised_failure_owner_access" for v in violations), (
        f"Expected test_serialised_failure_owner_access, got: {violations}"
    )
    assert (
        "unavailable_timeouts"
        in next(v for v in violations if v.rule == "test_serialised_failure_owner_access").detail
    )


def test_test_bare_snapshot_private_backoff_read_is_flagged() -> None:
    """The bare snapshot() spelling reaches the same private state and is rejected."""
    source = """
def test_snapshot():
    attempts = snapshot()["backoff_attempts"]
"""
    violations = audit_test_source("tests/recovery/test_sample.py", source)
    assert any(v.rule == "test_serialised_failure_owner_access" for v in violations), (
        f"Expected test_serialised_failure_owner_access, got: {violations}"
    )


def test_test_direct_and_serialised_private_failure_owner_reads_are_flagged() -> None:
    """Both coupling forms are rejected: the private attribute and its snapshot key."""
    source = """
def test_backoff(controller):
    assert controller._backoff_attempts.get("development:claude") == 2
    assert controller.snapshot()["backoff_attempts"] == {}
"""
    violations = audit_test_source("tests/recovery/test_sample.py", source)
    rules = {v.rule for v in violations}
    assert "test_private_failure_owner_access" in rules, (
        f"Expected the direct form, got: {violations}"
    )
    assert "test_serialised_failure_owner_access" in rules, (
        f"Expected the serialised form, got: {violations}"
    )


def test_test_public_controller_seam_is_accepted() -> None:
    """The controller's public seam stays observable from a test."""
    source = """
def test_controller_public_seam(controller):
    controller.handle(event)
    controller.reset_backoff("development", "opencode")
    assert controller.preferred_agent_index("development", ["opencode"]) == 0
    assert controller.unavailability_store.is_available("development", "opencode")
    assert controller.ledger.status("development", "opencode").is_spent is False
"""
    violations = audit_test_source("tests/recovery/test_sample.py", source)
    assert not violations


def test_test_controller_snapshot_non_failure_owner_key_is_accepted() -> None:
    """A snapshot key that is not private failure-owner state stays legal."""
    source = """
def test_controller_snapshot(controller):
    assert controller.snapshot()["cycle_cap"] == 5
"""
    violations = audit_test_source("tests/recovery/test_sample.py", source)
    assert not violations


def test_test_unrelated_private_reads_are_not_flagged() -> None:
    """Private reads that are not failure-owner state stay accepted."""
    source = """
class FakeRunner:
    def __init__(self):
        self._fake_counter = 0
        self._state = {}

def test_unrelated_private_reads(controller, runner, subprocess_runner, resolver_module):
    controller._write_session_reset_hint("development", failure)
    runner._integrate_on_phase_transition(state)
    assert runner._PHASE_TRANSITION_INTEGRATION_EVENTS
    controller._reset_controller_source_tree_cache()
    assert subprocess_runner._UNREPORTED_RETURNCODE != 0
    resolver_module._mirror_cursor_home(source, target)
"""
    violations = audit_test_source("tests/recovery/test_sample.py", source)
    assert not violations
