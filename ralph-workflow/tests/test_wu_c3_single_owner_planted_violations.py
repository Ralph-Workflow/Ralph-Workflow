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
