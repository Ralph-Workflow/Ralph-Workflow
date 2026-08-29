"""Conflict-resolution outcomes must classify, not fall through to ambiguous.

AMBIGUOUS is the classifier's last-resort bucket: it means no signature
matched, and it is logged as an unrecognised fault flagged for review.
Conflict resolution knows exactly what happened -- it types every
outcome itself -- but it has no exception object to hand over, so its
typed reasons arrived as bare prose and were reported as the one thing
they are not: unrecognised.
"""

from __future__ import annotations

from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.failure_classifier import FailureClassifier

_PHASE = "rebase_conflict_resolution"


def test_a_failed_conflict_attempt_is_an_agent_fault_not_an_unknown_one() -> None:
    classified = FailureClassifier().classify(
        "conflict attempt failed", phase=_PHASE, agent="pi"
    )
    assert classified.category is FailureCategory.AGENT
    # Not counted: an attempt that failed is answered by the next
    # candidate, which is exactly what the conflict chain does.
    assert classified.counts_against_budget is False


def test_a_candidate_that_never_started_is_an_agent_fault() -> None:
    classified = FailureClassifier().classify(
        "candidate produced no activity before it exited", phase=_PHASE, agent="pi"
    )
    assert classified.category is FailureCategory.AGENT
    assert classified.counts_against_budget is False


def test_pi_running_out_of_context_is_classified() -> None:
    """The executor parks only the class NAME, which is all we ever get."""
    classified = FailureClassifier().classify(
        "PiContextExhaustedExitError", phase=_PHASE, agent="pi"
    )
    assert classified.category is FailureCategory.AGENT
    assert classified.counts_against_budget is False


def test_an_unreachable_provider_is_environmental_not_the_agents_fault() -> None:
    classified = FailureClassifier().classify(
        "PiProviderFailureExitError", phase=_PHASE, agent="pi"
    )
    assert classified.category is FailureCategory.ENVIRONMENTAL


def test_an_agent_the_registry_cannot_produce_is_configuration() -> None:
    classified = FailureClassifier().classify("AgentNotFound", phase=_PHASE, agent="pi")
    assert classified.category is FailureCategory.USER_CONFIG


def test_genuinely_unrecognised_text_is_still_ambiguous() -> None:
    """The bucket must keep meaning "nothing matched"."""
    classified = FailureClassifier().classify(
        "an error nobody has ever written down", phase=_PHASE, agent="pi"
    )
    assert classified.category is FailureCategory.AMBIGUOUS


def test_every_typed_conflict_reason_classifies() -> None:
    """No reason the resolver can report may reach the unknown bucket."""
    from ralph.pipeline.conflict_resolution.session import ATTEMPT_FAILED_EVIDENCE

    classifier = FailureClassifier()
    for raw in (
        ATTEMPT_FAILED_EVIDENCE,
        "candidate produced no activity before it exited",
        "PiContextExhaustedExitError",
        "PiProviderFailureExitError",
        "BrokenAgentExitError",
        "MissingCredentialsError",
        "AgentNotFound",
    ):
        classified = classifier.classify(raw, phase=_PHASE, agent="pi")
        assert classified.category is not FailureCategory.AMBIGUOUS, raw
