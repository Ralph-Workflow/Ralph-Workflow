from __future__ import annotations

from pathlib import Path

from ralph.phases.required_artifacts import (
    build_retry_hint,
    build_validation_retry_hint,
)
from ralph.pipeline.effect_executor import write_agent_retry_prompt
from ralph.prompts import commit_review, materialize
from ralph.recovery.retry_prompt import (
    PREMISE_CHECK_LINE,
    build_retry_error_block,
    build_validation_retry_footer,
    failure_fingerprint,
    premise_check_line,
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_retry_prompt_still_exposes_the_formatter_contract": (
        "the premise-check rule is re-landed on runtime-owned retry state, so the ordinary "
        "retry formatter contract stays exported"
    ),
    "test_failure_fingerprint_is_stable_per_failure_and_differs_across_failures": (
        "the premise-check rule is re-landed on runtime-owned retry state, so the failure "
        "fingerprint stays stable per failure and differs across failures"
    ),
    "test_validation_retry_history_keeps_the_rendered_trailer_out_of_the_attempt_bodies": (
        "the premise-check rule is re-landed on runtime-owned retry state, so the persisted "
        "history is asserted against the rendered validation trailer"
    ),
    "test_persisted_hint_history_accumulates_attempts_across_repeats_and_changes": (
        "the premise-check rule is re-landed on runtime-owned retry state, so the persisted "
        "attempt history is asserted through the accumulated attempts"
    ),
    "test_generic_retry_hint_keeps_the_failure_summary_and_submission_instructions": (
        "the premise-check rule is re-landed on runtime-owned retry state, so the generic "
        "retry hint is asserted through its failure summary and submission instructions"
    ),
    "test_prompt_readers_stay_read_only": (
        "the premise-check rule is re-landed on runtime-owned retry state, so the runtime "
        "owns the retry state and the hint readers stay read-only"
    ),
    "test_retry_prompt_exposes_the_premise_check_contract": (
        "the premise-check rule is re-landed, so the retry prompt exposes the premise-check "
        "contract again"
    ),
    "test_premise_check_line_matches_only_an_identical_repeat_fingerprint": (
        "the premise-check rule is re-landed, so the premise line is emitted only for an "
        "identical repeat fingerprint"
    ),
    "test_validation_retry_hint_renders_the_premise_line_only_on_an_identical_repeat": (
        "the premise-check rule is re-landed, so the runtime-owned retry hint renders the "
        "premise line exactly once on an identical repeat and never on a first or changed "
        "failure"
    ),
    "test_validation_retry_hint_keeps_the_repeat_signal_across_three_identical_attempts": (
        "the premise-check rule is re-landed, so the premise line stays outside the attempt "
        "bodies and a third identical repeat still renders it"
    ),
    "test_generic_retry_prompt_renders_the_premise_line_only_on_an_identical_repeat": (
        "the premise-check rule is re-landed, so the generic agent-retry prompt renders the "
        "premise line exactly once on an identical repeat and never on a first or changed "
        "failure"
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


def test_retry_prompt_exposes_the_premise_check_contract() -> None:
    assert PREMISE_CHECK_LINE == "test the premise shared by the failed fixes before another patch"
    assert callable(premise_check_line)


def test_premise_check_line_matches_only_an_identical_repeat_fingerprint() -> None:
    fingerprint = failure_fingerprint("artifact rejected", "line 3 invalid")
    changed = failure_fingerprint("artifact rejected", "line 9 invalid")

    assert premise_check_line(fingerprint, fingerprint) == PREMISE_CHECK_LINE
    assert premise_check_line(fingerprint, changed) == ""
    assert premise_check_line(fingerprint, None) == ""
    assert premise_check_line("", fingerprint) == ""


def test_validation_retry_hint_renders_the_premise_line_only_on_an_identical_repeat() -> None:
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

    assert PREMISE_CHECK_LINE not in first
    assert repeat.count(PREMISE_CHECK_LINE) == 1
    assert PREMISE_CHECK_LINE not in changed


def test_validation_retry_hint_keeps_the_repeat_signal_across_three_identical_attempts() -> None:
    failure_a = [_Diagnostic("MD001", "frontmatter is malformed")]

    first = build_validation_retry_hint("development_result", failure_a)
    repeat = build_validation_retry_hint(
        "development_result",
        failure_a,
        prior_hint=first,
    )
    third = build_validation_retry_hint(
        "development_result",
        failure_a,
        prior_hint=repeat,
    )

    assert third.count(PREMISE_CHECK_LINE) == 1
    assert third.count("ATTEMPT 1") == 1
    assert third.count("ATTEMPT 2") == 1
    assert third.count("ATTEMPT 3") == 1
    assert third.count(build_validation_retry_footer()) == 1
    assert third.endswith(build_validation_retry_footer())


def test_generic_retry_prompt_renders_the_premise_line_only_on_an_identical_repeat(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("Repair the submitted artifact.\n", encoding="utf-8")

    def render(detail: str) -> str:
        retry_prompt = write_agent_retry_prompt(
            workspace_root=tmp_path,
            prompt_file=str(prompt_file),
            reason="AgentTimeout",
            detail=detail,
            context_lines=["the agent produced no output"],
            drain="development",
        )
        return Path(retry_prompt).read_text(encoding="utf-8")

    first = render("the agent produced no output")
    repeat = render("the agent produced no output")
    changed = render("a different failure happened")

    assert PREMISE_CHECK_LINE not in first
    assert repeat.count(PREMISE_CHECK_LINE) == 1
    assert PREMISE_CHECK_LINE not in changed
