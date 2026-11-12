from ralph.phases.required_artifacts import (
    PREMISE_CHECK_LINE,
    build_retry_hint,
    build_validation_retry_hint,
    failure_fingerprint,
    premise_check_line,
    retry_hint_fingerprint,
    validation_retry_fingerprint,
)
from ralph.prompts import commit_review, materialize
from ralph.recovery.retry_prompt import build_validation_retry_footer

RALPH_TEST_CHANGE_REASONS = {
    "test_premise_check_anchor_is_not_exported": (
        "the retired contract asserted the premise-check anchor is absent; the premise "
        "rule is re-landed, so the retry formatter must expose the anchor and render "
        "the premise line on an identical repeat"
    ),
}


class _Diagnostic:
    def __init__(self, rule_id: str, message: str) -> None:
        self.rule_id = rule_id
        self.message = message
        self.severity = "error"
        self.section = "frontmatter"
        self.line = 1


def test_retry_prompt_exposes_the_premise_check_contract() -> None:
    assert PREMISE_CHECK_LINE == "test the premise shared by the failed fixes before another patch"
    assert callable(premise_check_line)
    assert callable(failure_fingerprint)


def test_failure_fingerprint_is_stable_per_failure_and_differs_across_failures() -> None:
    first = failure_fingerprint("artifact rejected", "line 3 invalid")
    repeat = failure_fingerprint("artifact rejected", "line 3 invalid")
    changed = failure_fingerprint("artifact rejected", "line 9 invalid")
    assert first == repeat
    assert first != changed


def test_premise_check_line_renders_only_on_an_identical_repeat() -> None:
    fingerprint = failure_fingerprint("artifact rejected", "line 3 invalid")
    assert premise_check_line(fingerprint, None) == ""
    assert premise_check_line(fingerprint, "another-failure") == ""
    assert premise_check_line(fingerprint, fingerprint) == PREMISE_CHECK_LINE


def test_validation_retry_hint_renders_the_premise_line_only_on_an_identical_repeat() -> None:
    diagnostics = [_Diagnostic("MD001", "frontmatter is malformed")]
    fingerprint = validation_retry_fingerprint("development_result", diagnostics)
    first = build_validation_retry_hint("development_result", diagnostics)
    repeat = build_validation_retry_hint(
        "development_result",
        diagnostics,
        prior_fingerprint=fingerprint,
    )
    changed = build_validation_retry_hint(
        "development_result",
        [_Diagnostic("MD002", "body section is missing")],
        prior_fingerprint=fingerprint,
    )
    assert PREMISE_CHECK_LINE not in first
    assert PREMISE_CHECK_LINE in repeat
    assert PREMISE_CHECK_LINE not in changed


def test_generic_retry_hint_renders_the_premise_line_only_on_an_identical_repeat() -> None:
    fingerprint = retry_hint_fingerprint("development", "artifact was not submitted")
    first = build_retry_hint("development", "artifact was not submitted")
    repeat = build_retry_hint(
        "development",
        "artifact was not submitted",
        prior_fingerprint=fingerprint,
    )
    changed = build_retry_hint(
        "development",
        "a different failure happened",
        prior_fingerprint=fingerprint,
    )
    assert PREMISE_CHECK_LINE not in first
    assert PREMISE_CHECK_LINE in repeat
    assert PREMISE_CHECK_LINE not in changed


def test_persisted_hint_history_keeps_the_premise_line_off_a_changed_failure() -> None:
    failure_a = [_Diagnostic("MD001", "frontmatter is malformed")]
    failure_b = [_Diagnostic("MD002", "body section is missing")]
    fingerprint_a = validation_retry_fingerprint("development_result", failure_a)

    first = build_validation_retry_hint("development_result", failure_a)
    repeat = build_validation_retry_hint(
        "development_result",
        failure_a,
        prior_hint=first,
        prior_fingerprint=fingerprint_a,
    )
    changed = build_validation_retry_hint(
        "development_result",
        failure_b,
        prior_hint=repeat,
        prior_fingerprint=fingerprint_a,
    )

    assert first.count(PREMISE_CHECK_LINE) == 0
    assert repeat.count(PREMISE_CHECK_LINE) == 1
    assert changed.count(PREMISE_CHECK_LINE) == 0


def test_validation_retry_history_keeps_the_rendered_trailer_out_of_the_attempt_bodies() -> None:
    failure_a = [_Diagnostic("MD001", "frontmatter is malformed")]
    fingerprint_a = validation_retry_fingerprint("development_result", failure_a)

    first = build_validation_retry_hint("development_result", failure_a)
    repeat = build_validation_retry_hint(
        "development_result",
        failure_a,
        prior_hint=first,
        prior_fingerprint=fingerprint_a,
    )

    assert repeat.count(build_validation_retry_footer()) == 1
    assert repeat.endswith(PREMISE_CHECK_LINE)


def test_prompt_readers_stay_read_only() -> None:
    assert hasattr(materialize, "read_retry_hint")
    assert not hasattr(materialize, "read_and_clear_retry_hint")
    assert hasattr(commit_review, "_read_retry_hint")
    assert not hasattr(commit_review, "_read_and_clear_retry_hint")
