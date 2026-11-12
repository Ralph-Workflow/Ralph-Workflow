from ralph.phases import required_artifacts
from ralph.phases.required_artifacts import (
    build_retry_hint,
    build_validation_retry_hint,
    failure_fingerprint,
    retry_hint_fingerprint,
    validation_retry_fingerprint,
)
from ralph.prompts import commit_review, materialize
from ralph.recovery import retry_prompt

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_retry_prompt_exposes_the_premise_check_contract": (
        "the retired premise-check runtime policy was removed, so the contract no "
        "longer exports premise-check constants or functions"
    ),
    "test_premise_check_line_renders_only_on_an_identical_repeat": (
        "the premise_check_line function was removed as part of retiring premise-check policy"
    ),
    "test_validation_retry_hint_renders_the_premise_line_only_on_an_identical_repeat": (
        "validation retry hints no longer render the retired premise-check line on repeated failures"
    ),
    "test_generic_retry_hint_renders_the_premise_line_only_on_an_identical_repeat": (
        "generic retry hints no longer render the retired premise-check line on repeated failures"
    ),
    "test_premise_check_anchor_is_not_exported": (
        "asserts the absence of the retired premise-check exports and anchors"
    ),
    "test_validation_retry_hint_omits_the_premise_line_even_on_an_identical_repeat": (
        "validation retry hints assert the absence of the retired premise-check line on repeat"
    ),
    "test_generic_retry_hint_omits_the_premise_line_even_on_an_identical_repeat": (
        "generic retry hints assert the absence of the retired premise-check line on repeat"
    ),
}


class _Diagnostic:
    def __init__(self, rule_id: str, message: str) -> None:
        self.rule_id = rule_id
        self.message = message
        self.severity = "error"
        self.section = "frontmatter"
        self.line = 1


def test_premise_check_anchor_is_not_exported() -> None:
    assert not hasattr(retry_prompt, "PREMISE_CHECK_BANNER")
    assert not hasattr(retry_prompt, "PREMISE_CHECK_INSTRUCTION")
    assert not hasattr(retry_prompt, "PREMISE_CHECK_LINE")
    assert not hasattr(retry_prompt, "PREMISE_LINE")
    assert not hasattr(retry_prompt, "build_premise_check_block")
    assert not hasattr(required_artifacts, "PREMISE_CHECK_LINE")
    assert not hasattr(required_artifacts, "premise_check_line")


def test_retry_prompt_still_exposes_the_formatter_contract() -> None:
    assert hasattr(retry_prompt, "build_validation_retry_footer")
    assert hasattr(retry_prompt, "build_retry_error_block")
    assert hasattr(required_artifacts, "build_validation_retry_hint")
    assert hasattr(required_artifacts, "build_retry_hint")
    assert callable(failure_fingerprint)


def test_failure_fingerprint_is_stable_per_failure_and_differs_across_failures() -> None:
    first = failure_fingerprint("artifact rejected", "line 3 invalid")
    repeat = failure_fingerprint("artifact rejected", "line 3 invalid")
    changed = failure_fingerprint("artifact rejected", "line 9 invalid")
    assert first == repeat
    assert first != changed


def test_validation_retry_hint_omits_the_premise_line_even_on_an_identical_repeat() -> None:
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
    premise_line = "test the premise shared by the failed fixes before another patch"
    assert premise_line not in first
    assert premise_line not in repeat
    assert premise_line not in changed


def test_generic_retry_hint_omits_the_premise_line_even_on_an_identical_repeat() -> None:
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
    premise_line = "test the premise shared by the failed fixes before another patch"
    assert premise_line not in first
    assert premise_line not in repeat
    assert premise_line not in changed


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
