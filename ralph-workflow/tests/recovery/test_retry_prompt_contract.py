from __future__ import annotations

from ralph.phases.required_artifacts import (
    build_retry_hint,
    build_validation_retry_hint,
)
from ralph.prompts import commit_review, materialize
from ralph.recovery.retry_prompt import (
    build_retry_error_block,
    build_validation_retry_footer,
    failure_fingerprint,
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_retry_prompt_still_exposes_the_formatter_contract": (
        "the deleted agent rule is removed by the policy trim, so the ordinary retry "
        "formatter contract stays exported"
    ),
    "test_failure_fingerprint_is_stable_per_failure_and_differs_across_failures": (
        "the deleted agent rule is removed by the policy trim, so the failure fingerprint "
        "stays stable per failure and differs across failures"
    ),
    "test_validation_retry_history_keeps_the_rendered_trailer_out_of_the_attempt_bodies": (
        "the deleted agent rule is removed by the policy trim, so the persisted history is "
        "asserted against the rendered validation trailer instead of the retired instruction"
    ),
    "test_persisted_hint_history_accumulates_attempts_across_repeats_and_changes": (
        "the deleted agent rule is removed by the policy trim, so the persisted attempt "
        "history is asserted through the accumulated attempts instead of the retired instruction"
    ),
    "test_generic_retry_hint_keeps_the_failure_summary_and_submission_instructions": (
        "the deleted agent rule is removed by the policy trim, so the generic retry hint is "
        "asserted through its failure summary and submission instructions instead of the "
        "retired instruction"
    ),
    "test_prompt_readers_stay_read_only": (
        "the deleted agent rule is removed by the policy trim, so the runtime owns the retry "
        "state and the hint readers stay read-only"
    ),
}


class _Diagnostic:
    def __init__(self, rule_id: str, message: str) -> None:
        self.rule_id = rule_id
        self.message = message
        self.severity = "error"
        self.section = "frontmatter"
        self.line = 1


def test_retry_prompt_still_exposes_the_formatter_contract() -> None:
    assert callable(build_validation_retry_footer)
    assert callable(build_retry_error_block)
    assert callable(failure_fingerprint)


def test_failure_fingerprint_is_stable_per_failure_and_differs_across_failures() -> None:
    first = failure_fingerprint("artifact rejected", "line 3 invalid")
    repeat = failure_fingerprint("artifact rejected", "line 3 invalid")
    changed = failure_fingerprint("artifact rejected", "line 9 invalid")
    assert first == repeat
    assert first != changed


def test_validation_retry_history_keeps_the_rendered_trailer_out_of_the_attempt_bodies() -> None:
    failure_a = [_Diagnostic("MD001", "frontmatter is malformed")]
    first = build_validation_retry_hint("development_result", failure_a)
    repeat = build_validation_retry_hint(
        "development_result",
        failure_a,
        prior_hint=first,
    )

    assert repeat.count(build_validation_retry_footer()) == 1
    assert repeat.endswith(build_validation_retry_footer())
    assert repeat.count("ATTEMPT 1") == 1
    assert repeat.count("ATTEMPT 2") == 1


def test_persisted_hint_history_accumulates_attempts_across_repeats_and_changes() -> None:
    failure_a = [_Diagnostic("MD001", "frontmatter is malformed")]
    failure_b = [_Diagnostic("MD002", "body section is missing")]
    first = build_validation_retry_hint("development_result", failure_a)
    repeat = build_validation_retry_hint(
        "development_result",
        failure_a,
        prior_hint=first,
    )
    changed = build_validation_retry_hint(
        "development_result",
        failure_b,
        prior_hint=repeat,
    )

    assert first.count("ATTEMPT 1") == 1
    assert repeat.count("ATTEMPT 1") == 1
    assert repeat.count("ATTEMPT 2") == 1
    assert changed.count("ATTEMPT 1") == 1
    assert changed.count("ATTEMPT 2") == 1
    assert changed.count("ATTEMPT 3") == 1
    assert changed.count(build_validation_retry_footer()) == 1


def test_generic_retry_hint_keeps_the_failure_summary_and_submission_instructions() -> None:
    first = build_retry_hint("development", "artifact was not submitted")
    repeat = build_retry_hint(
        "development",
        "artifact was not submitted",
    )
    changed = build_retry_hint(
        "development",
        "a different failure happened",
    )

    assert first.count("Submit the artifact now.") == 1
    assert repeat.count("Submit the artifact now.") == 1
    assert changed.count("Submit the artifact now.") == 1
    assert first.count("PREVIOUS ATTEMPT FAILED") == 1
    assert repeat.count("PREVIOUS ATTEMPT FAILED") == 1
    assert changed.count("PREVIOUS ATTEMPT FAILED") == 1


def test_prompt_readers_stay_read_only() -> None:
    assert hasattr(materialize, "read_retry_hint")
    assert not hasattr(materialize, "read_and_clear_retry_hint")
    assert hasattr(commit_review, "_read_retry_hint")
    assert not hasattr(commit_review, "_read_and_clear_retry_hint")
