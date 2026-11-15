from __future__ import annotations

from ralph.testing.audit_agent_failure_single_owner import audit_production_source


def test_wu_c3_caught_agent_failure_regression_requires_cooldown_owner() -> None:
    source = """
def call_agent(telemetry):
    try:
        invoke_agent()
    except Exception as exc:
        failure = classify_failure(exc)
        telemetry.handle(failure)
"""
    violations = audit_production_source("ralph/pipeline/planted_catch.py", source)
    assert any(v.rule == "production_agent_failure_cooldown_bypass" for v in violations)


def test_wu_c3_unpenalised_classification_regression_requires_progress_allowlist() -> None:
    source = """
def classify_attempt():
    return ClassifiedFailure(
        category=category,
        reason="failed",
        attributed_agent="agent",
        attributed_phase="development",
        counts_against_budget=False,
        original_exception=None,
        raw_message="failed",
        is_unavailable=True,
    )
"""
    violations = audit_production_source("ralph/recovery/planted_failure.py", source)
    assert any(v.rule == "production_unjustified_non_penalising_failure" for v in violations)


def test_wu_c3_phase_local_failure_state_regression_is_rejected_outside_phases_package() -> None:
    source = """
class ResolutionPhase:
    def __init__(self):
        self.skipped_candidates = set()
        self.candidate_backoff = {}
"""
    violations = audit_production_source(
        "ralph/pipeline/conflict_resolution/planted_phase.py", source
    )
    assert any(v.rule == "production_phase_failure_state" for v in violations)


def test_wu_c3_hardcoded_round_cap_regression_is_rejected() -> None:
    source = """
def run_round(session):
    round_cap = session.max_rounds_per_stop or 3
    return round_cap
"""
    violations = audit_production_source(
        "ralph/pipeline/conflict_resolution/planted_rounds.py", source
    )
    assert any(v.rule == "production_hardcoded_round_cap" for v in violations)


def test_wu_c3_semantic_closure_regression_parameter_invoker_retry_loop_is_rejected() -> None:
    source = """
def run_candidates(invoke, candidates, retry_bound, sleeper):
    candidate_retries = {}
    for candidate in candidates:
        while True:
            invoke(candidate)
            attempts = candidate_retries.get(candidate, 0)
            if attempts < retry_bound:
                candidate_retries[candidate] = attempts + 1
                sleeper(0.25)
                continue
            break
"""
    violations = audit_production_source(
        "ralph/pipeline/conflict_resolution/planted_semantic_retry.py", source
    )
    assert any(v.rule == "production_phase_local_retry_loop" for v in violations)


def test_wu_c3_semantic_closure_regression_skip_and_delay_state_are_rejected() -> None:
    source = """
class AttemptState:
    skip_same_agent_retries = False
    dead_tool_surfaces = ()
    retry_delay_ms = 0
"""
    violations = audit_production_source(
        "ralph/pipeline/conflict_resolution/planted_semantic_state.py", source
    )
    assert sum(v.rule == "production_phase_failure_state" for v in violations) >= 3


def test_wu_c3_conflict_resolution_classified_failure_construction_is_rejected() -> None:
    source = """
from ralph.recovery.classified_failure import ClassifiedFailure

def build_failure(agent_name, category):
    return ClassifiedFailure(
        category=category,
        reason="failed",
        attributed_agent=agent_name,
        attributed_phase="resolution",
        counts_against_budget=True,
        original_exception=None,
        raw_message="failed",
        is_unavailable=False,
    )
"""
    violations = audit_production_source(
        "ralph/pipeline/conflict_resolution/planted_classification.py", source
    )
    assert any(
        v.rule == "production_conflict_resolution_classification_construction" for v in violations
    ), f"Expected production_conflict_resolution_classification_construction, got: {violations}"


def test_wu_c3_conflict_resolution_falsy_budget_decision_is_rejected() -> None:
    source = """
def decide(session, attempt):
    counts_against_budget = False
    return counts_against_budget
"""
    violations = audit_production_source(
        "ralph/pipeline/conflict_resolution/planted_budget.py", source
    )
    assert any(v.rule == "production_conflict_resolution_budget_binding" for v in violations), (
        f"Expected production_conflict_resolution_budget_binding, got: {violations}"
    )


def test_wu_c3_conflict_resolution_same_agent_retry_counter_is_rejected() -> None:
    source = """
def run_one_round(session, candidates):
    candidate_retries = {}
    for candidate in candidates:
        candidate_retries[candidate] = candidate_retries.get(candidate, 0) + 1
    return candidate_retries
"""
    violations = audit_production_source(
        "ralph/pipeline/conflict_resolution/planted_retry_counter.py", source
    )
    assert any(
        v.rule == "production_conflict_resolution_private_failure_state"
        and "candidate_retries" in v.detail
        for v in violations
    ), f"Expected production_conflict_resolution_private_failure_state, got: {violations}"


def test_wu_c3_conflict_resolution_retry_delay_computation_is_rejected() -> None:
    source = """
def wait_before_retry(session, policy_bundle):
    delay_ms = session.last_retry_delay_ms
    if delay_ms <= 0:
        delay_ms = policy_bundle.agents.agent_drains.get("resolution").retry_delay_ms
    return delay_ms
"""
    violations = audit_production_source(
        "ralph/pipeline/conflict_resolution/planted_retry_delay.py", source
    )
    assert any(
        v.rule == "production_conflict_resolution_private_failure_state" and "delay_ms" in v.detail
        for v in violations
    ), f"Expected production_conflict_resolution_private_failure_state, got: {violations}"


def test_wu_c3_conflict_resolution_session_private_state_is_rejected() -> None:
    source = """
class ResolutionSession:
    dead_tool_surfaces: tuple[str, ...] = ()
    last_retry_delay_ms: int = 0
    skip_same_agent_retries: bool = False
"""
    violations = audit_production_source("ralph/pipeline/conflict_resolution/session.py", source)
    assert sum(v.rule == "production_phase_failure_state" for v in violations) >= 3, (
        f"Expected production_phase_failure_state for the resolver session state, got: {violations}"
    )


def test_wu_c3_conflict_resolution_candidate_exclusion_list_is_rejected() -> None:
    source = """
def bar_agent(session, agent_name):
    session.dead_tool_surfaces = (*session.dead_tool_surfaces, agent_name)
"""
    violations = audit_production_source(
        "ralph/pipeline/conflict_resolution/chain_candidates.py", source
    )
    assert any(v.rule == "production_phase_failure_state" for v in violations), (
        f"Expected production_phase_failure_state for the resolver exclusion list, got: {violations}"
    )


def test_wu_c3_conflict_resolution_shared_recovery_reads_are_accepted() -> None:
    source = """
def wait_for_candidate(controller, ledger, candidates):
    delay = controller.earliest_available_wait_ms("resolution", candidates)
    status = ledger.status("resolution", "agent")
    if not status.is_eligible:
        return None
    controller.reset_backoff("resolution", "agent")
    return delay
"""
    violations = audit_production_source(
        "ralph/pipeline/conflict_resolution/planted_shared_reads.py", source
    )
    assert not violations, f"Expected shared recovery reads to be accepted, got: {violations}"


def test_wu_c3_shared_recovery_owner_state_is_not_flagged() -> None:
    source = """
class RecoveryController:
    def __init__(self):
        self._backoff_attempts = {}
        self._unavailability_tracker = build_tracker()
        self._cooldown_until = {}
"""
    violations = audit_production_source("ralph/recovery/controller.py", source)
    assert not violations, f"Expected the shared recovery owner to be accepted, got: {violations}"


def test_wu_c3_semantic_closure_regression_owner_like_name_is_not_type_evidence() -> None:
    source = """
def call_agent(recovery_owner):
    try:
        invoke_agent()
    except Exception as exc:
        failure = classify_failure(exc)
        recovery_owner.handle(failure)
"""
    violations = audit_production_source("ralph/pipeline/planted_fake_owner.py", source)
    assert any(v.rule == "production_agent_failure_cooldown_bypass" for v in violations)


def test_wu_c3_semantic_closure_recovery_controller_annotation_is_owner_evidence() -> None:
    source = """
from ralph.recovery.controller import RecoveryController

def call_agent(controller: RecoveryController):
    try:
        invoke_agent()
    except Exception as exc:
        failure = classify_failure(exc)
        controller.handle(failure)
"""
    violations = audit_production_source("ralph/pipeline/planted_real_owner.py", source)
    assert not [v for v in violations if "failure_cooldown_bypass" in v.rule]


def test_wu_c3_semantic_closure_unrelated_numeric_constant_is_not_round_cap() -> None:
    source = """
def render_round(round_index):
    padding = 3
    return round_index + padding
"""
    violations = audit_production_source("ralph/display/planted_round_label.py", source)
    assert not [v for v in violations if v.rule == "production_hardcoded_round_cap"]
