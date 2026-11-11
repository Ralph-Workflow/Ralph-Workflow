from __future__ import annotations

from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.failure_classifier import FailureClassifier

_PHASE = "rebase_conflict_resolution"


def test_a_failed_conflict_attempt_is_an_agent_fault_not_an_unknown_one() -> None:
    classified = FailureClassifier().classify("conflict attempt failed", phase=_PHASE, agent="pi")
    assert classified.category is FailureCategory.AGENT
    assert classified.counts_against_budget is False


def test_a_candidate_that_never_started_is_an_agent_fault() -> None:
    classified = FailureClassifier().classify(
        "candidate produced no activity before it exited", phase=_PHASE, agent="pi"
    )
    assert classified.category is FailureCategory.AGENT
    assert classified.counts_against_budget is False


def test_pi_running_out_of_context_is_classified() -> None:
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
    classified = FailureClassifier().classify(
        "an error nobody has ever written down", phase=_PHASE, agent="pi"
    )
    assert classified.category is FailureCategory.AMBIGUOUS


def test_every_typed_conflict_reason_classifies() -> None:
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


def test_retained_integration_is_recognized_without_blaming_commit_agent() -> None:
    failure = FailureClassifier().classify(
        "integration conflict requires resolution: integration resolution interrupted; retained for agent continuation",
        phase="development_commit",
        agent="committer",
    )
    assert str(failure.category) == "integration"
    assert not failure.counts_against_budget
    assert failure.attributed_agent is None
