from __future__ import annotations

from pathlib import Path

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.state import PipelineState
from ralph.recovery.classifier import FailureContext
from ralph.recovery.conflict_attempt_outcome import ConflictAttemptOutcome
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.failure_classifier import FailureClassifier
from ralph.recovery.unavailability_reason import UnavailabilityReason
from ralph.testing.audit_agent_failure_single_owner import audit_production_source

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_shared_classifier_regression_owns_every_typed_conflict_outcome": (
        "a no-progress success that went quiet after activity cools its resolver with the "
        "typed no_output_after_activity reason, like a stalled attempt"
    ),
}

_OWNED_CLASSIFICATIONS = (
    (
        ConflictAttemptOutcome.NO_ACTIVITY,
        FailureCategory.AGENT,
        True,
        True,
        UnavailabilityReason.NO_OUTPUT_AT_START,
    ),
    (
        ConflictAttemptOutcome.STALLED,
        FailureCategory.AGENT,
        True,
        True,
        UnavailabilityReason.NO_OUTPUT_AFTER_ACTIVITY,
    ),
    (
        ConflictAttemptOutcome.STALLED_NO_PROGRESS,
        FailureCategory.AGENT,
        True,
        True,
        UnavailabilityReason.NO_OUTPUT_AFTER_ACTIVITY,
    ),
    (
        ConflictAttemptOutcome.UNRESOLVED_REMAINING,
        FailureCategory.AGENT,
        True,
        False,
        None,
    ),
    (
        ConflictAttemptOutcome.ATTEMPT_FAILED,
        FailureCategory.AGENT,
        False,
        False,
        None,
    ),
)

_CLASSIFICATION_RULE = "production_conflict_resolution_classification_construction"
_BUDGET_BINDING_RULE = "production_conflict_resolution_budget_binding"


@pytest.mark.parametrize(
    ("outcome", "category", "counts", "unavailable", "unavailability_reason"),
    _OWNED_CLASSIFICATIONS,
)
def test_shared_classifier_regression_owns_every_typed_conflict_outcome(
    outcome: ConflictAttemptOutcome,
    category: FailureCategory,
    counts: bool,
    unavailable: bool,
    unavailability_reason: UnavailabilityReason | None,
) -> None:
    classified = FailureClassifier().classify_outcome(
        outcome,
        phase=PHASE_RESOLUTION,
        agent="primary",
        unresolved_count=2,
    )

    assert (
        classified.category,
        classified.counts_against_budget,
        classified.is_unavailable,
        classified.unavailability_reason,
    ) == (category, counts, unavailable, unavailability_reason)
    assert set(ConflictAttemptOutcome) - {row[0] for row in _OWNED_CLASSIFICATIONS} == {
        ConflictAttemptOutcome.RAW
    }


@pytest.mark.parametrize(
    ("outcome", "_category", "_counts", "_unavailable", "_reason"),
    _OWNED_CLASSIFICATIONS,
)
def test_recovery_controller_regression_matches_shared_typed_classification(
    outcome: ConflictAttemptOutcome,
    _category: FailureCategory,
    _counts: bool,
    _unavailable: bool,
    _reason: UnavailabilityReason | None,
) -> None:
    classifier = FailureClassifier()
    expected = classifier.classify_outcome(
        outcome,
        phase=PHASE_RESOLUTION,
        agent="primary",
        unresolved_count=2,
        raw_failure="external failure",
    )
    controller = RecoveryController(options=RecoveryControllerOptions(clock=FakeClock()))
    state = PipelineState(
        phase=PHASE_RESOLUTION,
        phase_chains={
            PHASE_RESOLUTION: AgentChainState(
                agents=["primary", "secondary"], current_index=0, retries=0
            )
        },
    )

    _new_state, _effects, event = controller.handle(
        state,
        "external failure",
        FailureContext(
            phase=PHASE_RESOLUTION,
            agent="primary",
            outcome=outcome,
            unresolved_count=2,
        ),
    )

    assert (
        event.category,
        event.reason,
        event.counted_against_budget,
        event.unavailability_reason,
    ) == (
        str(expected.category),
        expected.reason,
        expected.counts_against_budget,
        str(expected.unavailability_reason) if expected.unavailability_reason else None,
    )


def test_conflict_resolution_regression_audit_rejects_local_classification_and_falsy_budget() -> (
    None
):
    classified_source = """
from ralph.recovery.classified_failure import ClassifiedFailure as LocalFailure

def build(category, agent, phase):
    return LocalFailure(category, "reason", agent, phase, True, None, "raw")
"""
    budget_source = """
def alter(failure):
    return replace(failure, counts_against_budget=0 or False)
"""

    classified = audit_production_source(
        "ralph/pipeline/conflict_resolution/classifier.py", classified_source
    )
    budget = audit_production_source(
        "ralph/pipeline/conflict_resolution/classifier.py", budget_source
    )
    outside_scope = audit_production_source(
        "ralph/pipeline/plumbing/classifier.py", classified_source + budget_source
    )

    assert any(violation.rule == _CLASSIFICATION_RULE for violation in classified), classified
    assert any(violation.rule == _BUDGET_BINDING_RULE for violation in budget), budget
    assert not [
        violation
        for violation in outside_scope
        if violation.rule in {_CLASSIFICATION_RULE, _BUDGET_BINDING_RULE}
    ]


def test_conflict_resolution_regression_tree_has_no_local_classification_or_falsy_budget() -> None:
    project_root = Path(__file__).resolve().parents[2]
    conflict_root = project_root / "ralph" / "pipeline" / "conflict_resolution"
    violations = []
    for path in sorted(conflict_root.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "ClassifiedFailure" not in source and "counts_against_budget" not in source:
            continue
        rel_path = path.relative_to(project_root).as_posix()
        violations.extend(audit_production_source(rel_path, source))

    forbidden = [
        violation
        for violation in violations
        if violation.rule in {_CLASSIFICATION_RULE, _BUDGET_BINDING_RULE}
    ]
    assert not forbidden, forbidden
